// One ordered scenario over the same static build as level 1. /level2 enables the real command
// client only in this e2e build; page.route answers every command, while replay alone changes state.
import { expect, test } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph";

import { installRecorder, nodeIds, recorded } from "./graph-support";
import { engaged, installHolds, release } from "./holds";
import {
  a,
  added,
  approved,
  approvedProposal,
  b,
  bProposal,
  events,
  firstUpdate,
  hash,
  holds,
  mainAppend,
  proposal,
  requestedA,
  requestedB,
  reviewAppend,
  reviewEdit,
  revised,
} from "./level2-log";
import { appearance, append, interceptCommands, settledFrame, tapNode } from "./level2-support";

test("level 2 over replay: parallel agents, impact, revisions, edits, spend, stop and discard", async ({
  page,
  baseURL,
}) => {
  const errors: string[] = [];
  const urls: string[] = [];
  const colors: Partial<Record<"A" | "B", string>> = {};
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("request", (req) => urls.push(req.url()));
  const { sent, unexpected } = await interceptCommands(page);
  await page.addInitScript(installHolds, Object.values(holds));
  await page.addInitScript(installRecorder);
  const cardA = page.getByRole("article", { name: "Agent A" });
  const cardB = page.getByRole("article", { name: "Agent B" });
  const review = page.getByRole("complementary", { name: "Review panel" });
  const editable = review.getByLabel("Editable review file");
  const approve = review.getByRole("button", { name: "Approve and merge" });
  const request = review.getByRole("button", { name: "Request changes", exact: true });
  const comment = review.getByRole("textbox", { name: /Comment/ });

  await test.step("both cards work in parallel, with their letters, colors and node lights", async () => {
    // The backend sequence, rather than hand-authored ids, proves overlapping work and usage.
    for (const who of [a, b]) {
      const firstStep = events.find(
        (e) => e.type === "agent.step" && e.payload.agent_id === who.agent_id,
      );
      expect(firstStep?.seq).toBeLessThan(
        Math.min(...events.filter((e) => e.type === "change.proposed").map((e) => e.seq)),
      );
      expect(
        events.some((e) => e.type === "usage.tick" && e.payload.agent_id === who.agent_id),
      ).toBe(true);
    }
    await page.goto("/level2");
    await expect(cardA.getByRole("heading", { name: "Agent A" })).toBeVisible();
    await expect(cardB.getByRole("heading", { name: "Agent B" })).toBeVisible();
    for (const [slot, card] of [
      ["A", cardA],
      ["B", cardB],
    ] as const) {
      await expect(card.getByText("Working", { exact: true })).toBeVisible();
      const tint = await card
        .locator("header > span")
        .first()
        .evaluate((lamp, slot) => {
          const canvas = document.createElement("canvas");
          const ctx = canvas.getContext("2d");
          if (!ctx) throw new Error("No color reader");
          ctx.fillStyle = getComputedStyle(document.documentElement)
            .getPropertyValue(`--agent-${slot.toLowerCase()}`)
            .trim();
          ctx.fillRect(0, 0, 1, 1);
          const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
          return {
            actual: getComputedStyle(lamp).borderTopColor,
            expected: `rgb(${String(r)}, ${String(g)}, ${String(b)})`,
          };
        }, slot);
      expect(tint.actual).toBe(tint.expected);
      colors[slot] = tint.expected;
      await expect(card.getByRole("list", { name: /steps, newest first/ })).toContainText("Ok");
    }
    await engaged(page, holds.live);
    for (const slot of ["A", "B"]) {
      const edited = slot === "A" ? proposal.graph.nodes_modified : bProposal.graph.nodes_modified;
      expect(await nodeIds(page, `node.focus-${slot}-edit`)).toEqual(edited.toSorted());
      // Canvas pixels, not just classes: each edited/read node was painted in its agent's hue.
      await expect
        .poll(async () => {
          const samples = await recorded(page);
          return ["read", "edit"].every((mode) =>
            samples.some((sample) => (sample.lit[`${slot} ${mode}`]?.length ?? 0) > 0),
          );
        })
        .toBe(true);
      await expect
        .poll(async () => {
          const samples = await recorded(page);
          return edited.every(
            (id) => Math.max(0, ...samples.map((sample) => sample.paint[id] ?? 0)) >= 150,
          );
        })
        .toBe(true);
    }
    const strays = (await recorded(page)).map((sample) => sample.stray);
    expect(Math.max(...strays)).toBe(0);
    await expect(page.getByRole("complementary", { name: "Review panel" })).toHaveCount(0);
    await cardA.getByRole("button", { name: "Live diff" }).click();
    const live = page.getByRole("complementary", { name: "Live diff panel" });
    await expect(live.getByText("Read-only · Inline diff")).toBeVisible();
    await expect(live.locator(".monaco-diff-editor")).toBeVisible();
    await append(page, live.locator(".editor.modified"), "\nforbidden_live_edit = 1");
    await settledFrame(page);
    await expect(live.locator(".editor.modified .view-lines")).not.toContainText(
      "forbidden_live_edit",
    );
    await live.getByRole("button", { name: "Close live diff" }).click();
    await release(page, holds.live);
  });

  await test.step("both changes are pending; only pending nodes have dashed borders", async () => {
    await engaged(page, holds.decisions);
    // The existing cone assertions inspect individual functions, so choose that presentation.
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await expect
      .poll(() =>
        page
          .getByLabel("Repository graph", { exact: true })
          .evaluate((host: GraphProbeHost) => host.oversightGraph?.animating()),
      )
      .toBe(false);
    for (const [slot, card, change] of [
      ["A", cardA, proposal],
      ["B", cardB, bProposal],
    ] as const) {
      await expect(card.getByRole("status")).toHaveText("Ready for review");
      expect(await nodeIds(page, `node.pending-${slot}`)).toEqual(
        change.graph.nodes_modified.toSorted(),
      );
      const borders = await appearance(page, `node.pending-${slot}`);
      expect(borders.every((node) => node.borderStyle === "dashed")).toBe(true);
      expect(borders.map((node) => node.borderColor.replaceAll(" ", ""))).toEqual(
        borders.map(() => colors[slot]?.replaceAll(" ", "")),
      );
    }
    const nodes = await appearance(page, "node");
    expect(
      nodes
        .filter((node) => node.borderStyle === "dashed")
        .map((n) => n.id)
        .toSorted(),
    ).toEqual(await nodeIds(page, "node.pending"));
    // Possible calls keep solid lines even when a pending overlay is on the graph.
    expect((await appearance(page, "edge.possible")).every((e) => e.lineStyle === "solid")).toBe(
      true,
    );
    expect((await appearance(page, "node.pending-A"))[0]?.borderColor).not.toBe(
      (await appearance(page, "node.pending-B"))[0]?.borderColor,
    );
    await expect(page.getByRole("status", { name: "Review queue" })).toContainText("Decisions · 2");
    await expect(review).toHaveCount(0);
    await cardA.getByRole("button", { name: "Review change" }).click();
    await expect(review).toBeVisible();
    await expect(editable).toHaveAttribute("data-state", "clean");
    await expect(
      review
        .getByRole("region", { name: "Diff of shop/pricing.py" })
        .getByText("Editable · Inline diff"),
    ).toBeVisible();
    // This recording has test runs, but no behavior or caller comparisons (state d).
    await expect(review.getByRole("region", { name: "Review lead" })).toContainText(
      "Nothing was compared for this change.",
    );
    await expect(
      review
        .getByRole("region", { name: "Review lead" })
        .getByRole("heading", { name: "Nothing was compared for this change.", exact: true }),
    ).toHaveCount(0);
    await expect(review.getByRole("region", { name: "Review lead" })).toContainText(
      "Behavior and callers were not recorded for this revision. The diff and tests are the evidence.",
    );
    await expect(review.getByRole("listitem", { name: "Tests", exact: true })).toContainText(
      `${String(proposal.tests.passed)} passed`,
    );
    await expect(
      review.getByRole("region", { name: "What was compared", exact: true }),
    ).toHaveCount(0);
    await expect(review.getByRole("region", { name: "Review signals" })).toHaveCount(0);
    await expect(review.getByText("Details", { exact: true })).toHaveCount(0);
    const cone = page.getByRole("region", { name: "Possibly affected", exact: true });
    await expect(cone).toBeVisible();
    expect(proposal.impact.entries.length).toBeGreaterThan(0);
    expect(proposal.impact.omitted_by_depth).toBeGreaterThan(0);
    expect(proposal.impact.omitted_by_limit).toBeGreaterThan(0);
    for (const entry of proposal.impact.entries) {
      await expect(cone.locator(`[title="${entry.node_id}"]`)).toBeVisible();
      await expect(
        cone.locator(`span[aria-label="Distance ${String(entry.distance)}"]`),
      ).toBeVisible();
    }
    await expect(cone).toContainText(
      `+${String(proposal.impact.omitted_by_depth)} omitted by depth`,
    );
    await expect(cone).toContainText(
      `+${String(proposal.impact.omitted_by_limit)} omitted by the node limit`,
    );
    const lit = [
      ...proposal.impact.seeds,
      ...proposal.impact.entries.map((e) => e.node_id),
    ].toSorted();
    await expect
      .poll(() => nodeIds(page, "node.cone:childless"))
      .toEqual(proposal.impact.entries.map((entry) => entry.node_id).toSorted());
    const drawn = new Set(await nodeIds(page, "node"));
    for (const id of lit) {
      if (!drawn.has(id)) {
        // Added tests are evidence seeds; test nodes stay hidden on the graph.
        expect(proposal.graph.nodes_added.find((node) => node.id === id)?.is_test).toBe(true);
      } else {
        expect((await appearance(page, `node[id="${id}"]`))[0]?.opacity).toBe(1);
      }
    }
    expect((await appearance(page, "node.dimmed:childless")).every((n) => n.opacity < 1)).toBe(
      true,
    );
    await expect(review.getByLabel("Impact cone", { exact: true })).toHaveText(
      "1 possibly affected · up to distance 1",
    );
    await expect(approve).toBeEnabled();
    await expect(review.getByRole("button", { name: "Reject", exact: true })).toBeEnabled();
    await expect(request).toBeDisabled();
    await comment.fill("   ");
    await expect(request).toBeDisabled();
    await comment.fill(requestedA.payload.comment ?? "");
    await comment.press("Enter");
    await comment.press("Control+Enter");
    await expect(comment).toBeFocused();
    await expect(request).toBeEnabled();
    await settledFrame(page);
    expect(sent).toEqual([]);
    await request.click();
    await expect(request).toBeDisabled();
    await expect(review.getByText("Requesting changes: waiting for the agent.")).toBeVisible();
    await expect
      .poll(() => sent)
      .toEqual([
        {
          path: `/changes/${a.change_id}/request-changes`,
          method: "POST",
          body: {
            revision: proposal.revision,
            head_commit: proposal.head_commit,
            comment: requestedA.payload.comment,
          },
        },
      ]);
    // HTTP 202 alone changes no visible state: only the replay's resolution does.
    await expect(review.getByText("Ready for review", { exact: true })).toBeVisible();
    await release(page, holds.decisions);
  });

  await test.step("A revises the same change, disabling editing until the higher revision", async () => {
    await engaged(page, holds.revising);
    await expect(page.getByRole("group", { name: "Agent A: Revising", exact: true })).toBeVisible();
    await expect(review.getByText("Read-only while the agent is not ready")).toBeVisible();
    await expect(approve).toBeDisabled();
    await expect(review.getByRole("button", { name: "Save review edits" })).toBeDisabled();
    await append(page, review.locator(".editor.modified"), "\nforbidden_revision_edit = 1");
    await settledFrame(page);
    await expect(review.locator(".editor.modified .view-lines")).not.toContainText(
      "forbidden_revision_edit",
    );
    await release(page, holds.revising);
    await engaged(page, holds.edit);
    expect(revised.payload.change_id).toBe(proposal.change_id);
    expect(revised.payload.revision).toBeGreaterThan(proposal.revision);
    await expect(approve).toBeEnabled();
    await expect(
      review
        .getByRole("region", { name: "Diff of shop/pricing.py" })
        .getByText("Editable · Inline diff"),
    ).toBeVisible();
    // Close/reopen review loads the actual new revision; live diff never becomes editable.
    await review.getByRole("button", { name: "Close review" }).click();
    await cardA.getByRole("button", { name: "Review change" }).click();
    await expect(editable).toHaveAttribute("data-state", "clean");
    await append(page, review.locator(".editor.modified"), reviewAppend);
    await expect(review.getByText("Unsaved edits")).toBeVisible();
    await expect(approve).toBeDisabled();
    await expect(review.getByRole("button", { name: "Save review edits" })).toBeEnabled();
    await review.getByRole("button", { name: "Save review edits" }).click();
    await expect(editable).toHaveAttribute("data-state", "clean");
    const previous = events.findLast(
      (e) =>
        e.type === "change.updated" && e.seq < reviewEdit.seq && e.payload.agent_id === a.agent_id,
    );
    if (previous?.type !== "change.updated") throw new Error("Missing review base");
    expect(sent.at(-1)).toEqual({
      path: "/files",
      method: "PUT",
      body: {
        path: firstUpdate.file.path,
        agent_id: a.agent_id,
        base_hash: hash(previous.payload.file.modified ?? ""),
        content: reviewEdit.payload.file.modified,
      },
    });
    await release(page, holds.edit);
    await engaged(page, holds.saved);
    // Monaco virtualizes off-screen lines; navigate to the saved footer explicitly.
    await review
      .locator(".editor.modified :is(.native-edit-context, textarea.inputarea)")
      .last()
      .focus();
    await page.keyboard.press("Control+End");
    await expect(review.locator(".editor.modified .view-lines")).toContainText(
      "# Reviewed by the developer.",
    );
    await expect(approve).toBeEnabled();
    await review.getByRole("button", { name: "Close review" }).click();
  });

  await test.step("spend uses both agents; stop and discard requires confirmation on B's card", async () => {
    // Only ticks replayed so far count. Each card must show its own sum, including A's revision.
    const ticks = events
      .filter((e) => e.type === "usage.tick")
      .filter((e) => e.seq < holds.saved.seq);
    const costs = [a, b].map((who) =>
      ticks
        .filter((e) => e.payload.agent_id === who.agent_id)
        .reduce((sum, e) => {
          if (e.payload.cost_usd === null) throw new Error("Recording needs known fake costs");
          return sum + e.payload.cost_usd;
        }, 0),
    );
    const figures = costs.map((cost) => `US$${cost.toFixed(2)}`);
    expect(costs.every((cost) => cost >= 0.005)).toBe(true);
    expect(figures[0]).not.toBe(figures[1]);
    await expect(page.getByRole("group", { name: "Spend", exact: true })).toHaveText(
      `Spent US$${costs.reduce((sum, cost) => sum + cost, 0).toFixed(2)} of US$150`,
    );
    for (const [index, card] of [cardA, cardB].entries())
      await expect(
        card
          .locator("dl > div")
          .filter({ has: page.getByText("Cost", { exact: true }) })
          .getByRole("definition"),
      ).toHaveText(figures[index] ?? "");
    // B's logged request resumes it, so cancellation is checked during work rather than after finish.
    await cardB.getByRole("button", { name: "Review change" }).click();
    await expect(editable).toHaveAttribute("data-state", "clean");
    await comment.fill(requestedB.payload.comment ?? "");
    await request.click();
    await expect
      .poll(() => sent.at(-1))
      .toEqual({
        path: `/changes/${b.change_id}/request-changes`,
        method: "POST",
        body: {
          revision: bProposal.revision,
          head_commit: bProposal.head_commit,
          comment: requestedB.payload.comment,
        },
      });
    await review.getByRole("button", { name: "Close review" }).click();
    await release(page, holds.saved);
    await engaged(page, holds.stop);
    await expect(cardB.getByText("Revising", { exact: true })).toBeVisible();
    const count = sent.length;
    await cardB.getByRole("button", { name: "Stop and discard", exact: true }).click();
    await expect(
      cardB.getByText("This stops the agent and discards its worktree and all pending edits."),
    ).toBeVisible();
    expect(sent).toHaveLength(count);
    await cardB.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(cardB.getByRole("button", { name: "Confirm stop and discard" })).toHaveCount(0);
    expect(sent).toHaveLength(count);
    await cardB.getByRole("button", { name: "Stop and discard", exact: true }).click();
    await cardB.getByRole("button", { name: "Confirm stop and discard" }).click();
    await expect
      .poll(() => sent.at(-1))
      .toEqual({ path: `/agents/${b.agent_id}/stop`, method: "POST", body: null });
    await expect(cardB.getByText("Stopping: waiting for the discarded change.")).toBeVisible();
    expect(await nodeIds(page, "node.pending-B")).toEqual(
      bProposal.graph.nodes_modified.toSorted(),
    );
    await release(page, holds.stop);
    await engaged(page, holds.approve);
    await expect(cardB.getByText("Stopped", { exact: true })).toBeVisible();
    await expect(cardB.getByRole("button", { name: "Stop and discard", exact: true })).toHaveCount(
      0,
    );
    expect(await nodeIds(page, "node.pending-B")).toEqual([]);
    expect(await nodeIds(page, "node.pending-A")).not.toEqual([]);
    await cardA.getByRole("button", { name: "Review change" }).click();
    await expect(editable).toHaveAttribute("data-state", "clean");
    await expect(approve).toBeEnabled();
    await approve.click();
    await expect
      .poll(() => sent.at(-1))
      .toEqual({
        path: `/changes/${a.change_id}/approve`,
        method: "POST",
        body: { revision: approvedProposal.revision, head_commit: approvedProposal.head_commit },
      });
    await release(page, holds.approve);
    await engaged(page, holds.main);
    await expect(review.getByText("Approved and merged into main")).toBeVisible();
    await expect(
      page.locator(`header span[title="${approved.payload.merge_commit ?? ""}"]`),
    ).toBeVisible();
    expect(await nodeIds(page, "node.pending")).toEqual([]);
    await review.getByRole("button", { name: "Close review" }).click();
  });

  await test.step("a file-view save writes main and the recorded delta adds the function", async () => {
    expect(await nodeIds(page, `node[id="${added.id}"]`)).toEqual([]);
    await tapNode(page, "shop.pricing:apply_coupon");
    const file = page.getByRole("complementary", { name: "File view" });
    await expect(file).toHaveAttribute("data-state", "clean");
    await append(page, file, mainAppend);
    // Monaco indents the blank line after return. Remove that indentation so the HTTP body is
    // exactly the edit captured by the backend, including its final newline.
    await page.keyboard.press("Shift+Home");
    await page.keyboard.press("Backspace");
    await expect(file.getByText("Unsaved edits")).toBeVisible();
    await file.getByRole("button", { name: "Save", exact: true }).click();
    await expect(file).toHaveAttribute("data-state", "clean");
    const base = reviewEdit.payload.file.modified ?? "";
    expect(sent.at(-1)).toEqual({
      path: "/files",
      method: "PUT",
      body: {
        path: added.path,
        base_hash: hash(base),
        content: base + mainAppend,
      },
    });
    expect(await nodeIds(page, `node[id="${added.id}"]`)).toEqual([]);
    await release(page, holds.main);
    await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events.length));
    await expect.poll(() => nodeIds(page, `node[id="${added.id}"]`)).toEqual([added.id]);
    await expect(file).toContainText(added.path);
    await file.getByRole("button", { name: "Close file" }).click();
    await expect(cardA.getByText("Done", { exact: true })).toBeVisible();
    expect(unexpected).toEqual([]);
    expect(errors).toEqual([]);
    const origin = new URL(baseURL ?? "").origin;
    expect(urls.filter((url) => new URL(url).origin !== origin)).toEqual([]);
    expect(sent.filter((req) => req.path.endsWith("/approve"))).toHaveLength(1);
  });
});
