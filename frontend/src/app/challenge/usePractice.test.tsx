import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import type { ChosenReplaySource } from "../../transport";
import { replaySource } from "../../transport";
import { App } from "../App";
import * as hooks from "./usePractice";

vi.mock("../../transport", async (original) => ({
  ...(await original<typeof import("../../transport")>()),
  replayBuild: true,
}));
vi.mock("../../editor/monaco", () => ({ monaco: { editor: {} } }));
afterEach(() => vi.restoreAllMocks());
const handwritten: ChosenReplaySource = {
  kind: "hand-written demo fixture",
  label: "Replay · hand-written demo fixture",
  text: "",
};
function Entry({ replay, source }: { replay: boolean; source: ChosenReplaySource }) {
  const practice = hooks.usePractice(vi.fn(), vi.fn(), vi.fn(), replay, source);
  return practice ? <button>Two-change practice</button> : null;
}
it.each([
  ["public recording", true, replaySource, true],
  ["handwritten replay seam", true, handwritten, false],
  ["level2 command seam", false, handwritten, false],
  ["live run", false, replaySource, false],
] as const)("uses App's replay configuration for the %s", (_, replay, source, available) => {
  const html = renderToStaticMarkup(<Entry replay={replay} source={source} />);
  expect(html.includes("Two-change practice")).toBe(available);
});
it("App passes the same replay configuration it uses for its status and commands", () => {
  const usePractice = vi.spyOn(hooks, "usePractice");
  renderToStaticMarkup(<App />);
  expect(usePractice).toHaveBeenCalledWith(
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    true,
    replaySource,
  );
});
