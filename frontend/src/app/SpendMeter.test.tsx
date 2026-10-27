import type { UsageTick } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { createInitialState, createStateStore } from "../state";
import type { State } from "../state";
import { startReplay } from "../transport";
import { Frame } from "./Frame";
import { through } from "./replay.fixture";
import { SpendMeter } from "./SpendMeter";
import { initialView } from "./view";

vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

function metered(usage: Partial<UsageTick> = {}): State {
  const state = through(1);
  if (!state.run) throw new Error("Missing run fixture");
  return {
    ...state,
    spendUnknown: usage.cost_usd === null,
    run: {
      ...state.run,
      budget: { budget_usd: 150, spent_usd: 10, warn_at: [0.5, 0.8], block_at: 0.9 },
    },
    usage: {
      agent_id: "agent-1",
      model: "muse-spark",
      input_tokens: 100,
      output_tokens: 10,
      cached_tokens: null,
      cost_usd: 2.4,
      spent_usd: 12.4,
      budget_usd: 150,
      brake: "ok",
      ...usage,
    },
  };
}

const render = (state: State) => renderToStaticMarkup(<SpendMeter state={state} />);
/** What a reader sees: the markup's text, one space between words. */
const words = (markup: string) =>
  markup
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();

describe("the top bar spend meter", () => {
  it.each([
    ["ok", null],
    ["warn_50", "50% of the budget used"],
    ["warn_80", "80% of the budget used"],
    ["blocked", "New Meta runs are blocked: the budget is nearly spent"],
  ] as const)("labels %s in words and an icon, using neutral tokens", (brake, caption) => {
    const markup = render(metered({ brake }));
    expect(words(markup)).toContain("Spent US$12.40 of US$150.00");
    expect(markup).toContain('role="group" aria-label="Spend"');
    expect(markup).toContain("font-mono text-(length:--data-size)");
    expect(markup).toContain('role="status" aria-atomic="true"');
    expect(markup).toContain("text-muted");
    expect(markup).not.toMatch(/text-(agent|passed|failed|amber)|#[0-9a-f]{6}/i);
    if (caption) {
      expect(markup).toContain(caption);
      expect(markup).toMatch(/<svg [^>]*aria-hidden="true"/);
      expect(markup).toContain(brake === "blocked" ? "lucide-ban" : "lucide-circle-alert");
      expect(markup).not.toContain(brake === "blocked" ? "lucide-circle-alert" : "lucide-ban");
    } else {
      expect(markup).not.toContain("<svg");
      expect(markup).not.toContain("budget used");
      expect(markup).not.toContain("New Meta runs");
    }
  });

  it("formats fractional budgets with the same two decimals as the total", () => {
    const state = metered({ budget_usd: 150.5 });
    expect(words(render(state))).toContain("Spent US$12.40 of US$150.50");
  });

  it("keeps the total's markup the same at every brake, with the caption before it", () => {
    const total = (brake: UsageTick["brake"]) =>
      /<p>Spent.*?<\/p>/.exec(render(metered({ brake })))?.[0];
    const ok = total("ok");
    expect(ok).toContain("Spent");
    for (const brake of ["warn_50", "warn_80", "blocked"] as const) expect(total(brake)).toBe(ok);
    // The status region is always mounted and never wraps, so a caption never moves the total.
    const blocked = render(metered({ brake: "blocked" }));
    expect(blocked.indexOf("New runs are blocked")).toBeLessThan(blocked.indexOf("Spent"));
    expect(blocked).toContain("whitespace-nowrap");
    expect(blocked).not.toContain("h-lh");
  });

  it("shows unknown rather than a zero or the known ledger subtotal for a null cost", () => {
    const markup = render(metered({ cost_usd: null, brake: "blocked" }));
    expect(words(markup)).toContain("Spent unknown of US$150.00");
    expect(words(markup)).not.toMatch(/Spent US\$/);
    expect(markup).toContain("New Meta runs are blocked: the budget is nearly spent");
    expect(words(render(metered({ cost_usd: 0, spent_usd: 0 })))).toContain(
      "Spent US$0.00 of US$150.00",
    );
  });

  it("shows the starting ledger total before any tick", () => {
    expect(words(render({ ...metered(), usage: null }))).toContain("Spent US$10.00 of US$150.00");
  });

  it("renders nothing without a run or a positive run budget", () => {
    expect(render(createInitialState())).toBe("");
    const state = metered();
    if (!state.run) throw new Error("Missing run fixture");
    state.run = { ...state.run, budget: { ...state.run.budget, budget_usd: 0 } };
    expect(render(state)).toBe("");
  });

  it.each([null, "change-1"])("stays in the top bar with review %s", (review) => {
    const markup = renderToStaticMarkup(
      <Frame
        state={metered()}
        view={{ ...initialView, review }}
        onNodeTap={vi.fn()}
        canOpenChange={() => false}
        onOpenChange={vi.fn()}
        onClosePanel={vi.fn()}
      />,
    );
    expect(words(markup.slice(0, markup.indexOf("</header>")))).toContain(
      "Spent US$12.40 of US$150.00",
    );
  });

  it("plays the shared replay log with its recorded totals and no backend", async () => {
    const store = createStateStore(metered({ brake: "blocked" }));
    const request = vi.fn<typeof fetch>();
    const replay = startReplay({ store, source: { text: fixture }, speed: 0, request });
    try {
      await replay.ready;
      const state = store.getState().state;
      expect(state.usage?.spent_usd).toBe(0.009794);
      expect(words(render(state))).toContain("Spent US$0.01 of US$50.00");
      expect(render(state)).not.toContain("New Meta runs are blocked");
      expect(request).not.toHaveBeenCalled();
    } finally {
      replay.stop();
    }
  });
});
