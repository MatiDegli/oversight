import { launchBrowser } from "../../browserLaunch.support";
import { readFileSync } from "node:fs";
import { expect as check } from "@playwright/test";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PhoneGraph } from "./PhoneGraph";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";

it.each(["A", "B"] as const)(
  "colors a new exact call and its arrow in the recorded agent %s color",
  async (slot) => {
    const practice = validateManifest(manifest, recording);
    const entry = practice.cases[0];
    const agent = entry && practice.cut.agents[entry.proposal.agent_id];
    if (!entry || !agent) throw new Error("Missing call case");
    agent.slot = slot;
    const browser = await launchBrowser({ headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.setContent(
        renderToStaticMarkup(
          <div className="phone-practice">
            <PhoneGraph practice={practice} entry={entry} merged={false} />
          </div>,
        ),
      );
      for (const path of ["../../theme/index.css", "./phone.css", "./phoneGraph.css"])
        await page.addStyleTag({ content: readFileSync(new URL(path, import.meta.url), "utf8") });
      const node = page.locator(".pp-node");
      const color = await node.evaluate((element) => getComputedStyle(element).borderTopColor);
      expect(color).not.toBe("rgba(0, 0, 0, 0)");
      await check(node).toHaveAttribute("data-slot", slot);
      await check(page.locator(".pp-taxi.is-pending")).toHaveCSS("stroke", color);
      await check(page.locator(".pp-taxi.is-pending .pp-arrow")).toHaveCSS("fill", color);
    } finally {
      await browser.close();
    }
  },
);
