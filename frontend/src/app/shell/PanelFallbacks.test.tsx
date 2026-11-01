import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ReviewFrame } from "./PanelFallbacks";
import { manifest, validateManifest } from "../challenge/manifest";
import { recording } from "../challenge/recording.fixture";

it.each([false, true])(
  "only labels rules merges backed by a proposal (proposal: %s)",
  (proposed) => {
    const practice = validateManifest(manifest, recording);
    const entry = practice.cases[0];
    if (!entry) throw new Error("Missing case");
    const state = structuredClone(practice.cut);
    const change = state.changes[entry.changeId];
    if (!change) throw new Error("Missing change");
    change.status = "approved";
    change.resolution = { ...entry.resolution, decided_by: "rules" };
    if (!proposed) change.proposal = null;
    const html = renderToStaticMarkup(
      <ReviewFrame state={state} changeId={entry.changeId} onClose={() => {}} />,
    );
    expect(html.includes('review-header-status">Merged</span>')).toBe(proposed);
    if (!proposed) expect(html).toContain('review-header-status">Approved</span>');
  },
);
