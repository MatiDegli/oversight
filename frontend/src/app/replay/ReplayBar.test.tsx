import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import log from "../../../../fixtures/events/recorded-demo.jsonl?raw";
import { parseEvent } from "../../transport";
import { replayMarks } from "./model";
import { ReplayTooltip } from "./ReplayBar";

it.each([1, 2])("describes a proposal with %i recorded files", (count) => {
  const events = log.trim().split("\n").map(parseEvent);
  const mark = replayMarks(events, []).find((mark) => mark.proposal);
  if (!mark?.proposal) throw new Error("No proposal in recording");
  const files = mark.proposal.files.slice(0, count);
  expect(files).toHaveLength(count);
  const html = renderToStaticMarkup(
    <ReplayTooltip mark={{ ...mark, proposal: { ...mark.proposal, files } }} percent="50%" />,
  );
  expect(html).toContain(` in ${String(count)} ${count === 1 ? "file<" : "files<"}`);
});
