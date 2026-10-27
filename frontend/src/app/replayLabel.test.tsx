import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

import { replaySource } from "../transport";
import { App } from "./App";
import { Frame } from "./Frame";
import { through } from "./replay.fixture";
import { initialView, openReview } from "./view";

const transport = vi.hoisted(() => ({ replayBuild: true }));
vi.mock("../transport", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../transport")>()),
  get replayBuild() {
    return transport.replayBuild;
  },
}));
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));
afterEach(() => {
  transport.replayBuild = true;
});

function expectHeaderLabel(markup: string) {
  const header = /<header\b[^>]*>(.*?)<\/header>/.exec(markup)?.[1];
  const main = /<main\b[^>]*>(.*?)<\/main>/.exec(markup)?.[1];
  expect(header).toContain(replaySource.label);
  expect(main).toBeDefined();
  expect(main).not.toContain(replaySource.label);
  expect(markup.split(replaySource.label)).toHaveLength(2);
}

it("renders the replay label once in the app header, outside the graph main", () => {
  expectHeaderLabel(renderToStaticMarkup(<App />));
});

it("keeps the replay label in the header while review is open", () => {
  const state = through(20);
  const view = openReview(state, initialView, "change-1");
  if (!view) throw new Error("Missing review fixture");
  expectHeaderLabel(
    renderToStaticMarkup(
      <Frame
        state={state}
        view={view}
        onNodeTap={vi.fn()}
        canOpenChange={() => false}
        onOpenChange={vi.fn()}
        onClosePanel={vi.fn()}
      />,
    ),
  );
});

it("leaves the recorded label out of the live app", () => {
  transport.replayBuild = false;
  expect(renderToStaticMarkup(<App />)).not.toContain(replaySource.label);
});
