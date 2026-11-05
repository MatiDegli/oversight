import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { presentationFor } from "../teams/presentation";
import { Example } from "./AnswerChoices";
import { ReviewBody } from "./ReviewBody";
import { ReviewHeader } from "./ReviewHeader";

const design = readFileSync(new URL("../../../DESIGN.md", import.meta.url), "utf8");
const state = readFileSync(
  new URL("../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map(parseEvent)
  .filter((event) => event.seq <= 62)
  .reduce(applyEvent, createInitialState());
const plain = (markup: string) => markup.replace(/<[^>]*>/g, "");

it("documents the built lone-agent review copy and retains plan-label examples", () => {
  const identity = presentationFor(state, "agent-2");
  expect(identity?.label).toBe("B");
  const header = plain(renderToStaticMarkup(<ReviewHeader state={state} changeId="change-2" />));
  const body = plain(
    renderToStaticMarkup(
      <ReviewBody state={state} changeId="change-2" diff={null} showCrossReview={false} />,
    ),
  );
  const example = plain(
    renderToStaticMarkup(
      <Example
        row={{ inputs: "x", before: "1", after: "2", unstable: false }}
        identity={identity}
      />,
    ),
  );
  for (const [built, copy] of [
    [header, "Agent B · Muse Spark Open"],
    [body, "What Agent B did"],
    [example, "After · Agent B"],
  ] as const) {
    expect(built).toContain(copy);
    expect(design).toContain(`"${copy}`);
  }
  expect(design).toContain('"Also changed by Agent B: ..."');
  expect(design).toContain("which Agent B is changing");
  expect(design).toContain("Ask Agent A to redo it on the new base");
  expect(design).not.toMatch(
    /B·1 says|After · B·1|B·1 · Muse Spark Open|Also changed by B·1|Ask A·1/,
  );
  expect(design).toContain('"Agent A·2"');
});
