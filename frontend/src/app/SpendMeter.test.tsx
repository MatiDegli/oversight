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

describe("the top bar spend meter", () => {
  it.each([
    ["ok", null],
    ["warn_50", "50% of the budget used"],
    ["warn_80", "80% of the budget used"],
    ["blocked", "New runs are blocked: the budget is nearly spent"],
  ] as const)("labels %s in words and an icon, using neutral tokens", (brake, caption) => {
    const markup = render(metered({ brake }));
    expect(markup).toContain("US$12.40 of 150.00");
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
      expect(markup).not.toContain("New runs");
    }
  });

  it("formats fractional budgets with the same two decimals as the total", () => {
    const state = metered({ budget_usd: 150.5 });
    expect(render(state)).toContain("US$12.40 of 150.50");
  });

  it.each<UsageTick["brake"]>(["ok", "warn_50", "warn_80", "blocked"])(
    "reserves one unwrapped caption line at %s so the total stays put",
    (brake) => {
      const markup = render(metered({ brake }));
      const status = markup.match(/<div role="status"[^>]*>/)?.[0];
      expect(status).toContain("h-lh");
      expect(status).toContain("whitespace-nowrap");
    },
  );

  it("shows unknown rather than a zero or the known ledger subtotal for a null cost", () => {
    const markup = render(metered({ cost_usd: null, brake: "blocked" }));
    expect(markup).toContain("unknown of 150.00");
    expect(markup).not.toContain("US$");
    expect(markup).toContain("New runs are blocked: the budget is nearly spent");
    expect(render(metered({ cost_usd: 0, spent_usd: 0 }))).toContain("US$0.00 of 150.00");
  });

  it("shows the starting ledger total before any tick", () => {
    expect(render({ ...metered(), usage: null })).toContain("US$10.00 of 150.00");
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
    expect(markup.slice(0, markup.indexOf("</header>"))).toContain("US$12.40 of 150.00");
  });

  it("plays the shared replay log with its recorded totals and no backend", async () => {
    const store = createStateStore(metered({ brake: "blocked" }));
    const request = vi.fn<typeof fetch>();
    const replay = startReplay({ store, source: { text: fixture }, speed: 0, request });
    try {
      await replay.ready;
      const state = store.getState().state;
      expect(state.usage?.spent_usd).toBe(0.009794);
      expect(render(state)).toContain("US$0.01 of 50.00");
      expect(render(state)).not.toContain("New runs are blocked");
      expect(request).not.toHaveBeenCalled();
    } finally {
      replay.stop();
    }
  });
});
