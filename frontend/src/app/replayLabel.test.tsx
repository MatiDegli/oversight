import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, expect, it, vi } from "vitest";

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
// Resolve the real lazy stage before the synchronous composition assertions.
beforeAll(async () => {
  const stream = await renderToReadableStream(
    <Frame
      state={through(2)}
      view={initialView}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
    />,
  );
  await stream.allReady;
  await new Response(stream).text();
  vi.clearAllMocks();
});

afterEach(() => {
  transport.replayBuild = true;
});

function expectStatusLabel(markup: string) {
  const header = /<header\b[^>]*>(.*?)<\/header>/.exec(markup)?.[1];
  const status = /<footer\b[^>]*aria-label="Status line"[^>]*>(.*?)<\/footer>/.exec(markup)?.[1];
  const main = /<main\b[^>]*>(.*?)<\/main>/.exec(markup)?.[1];
  expect(status).toContain(replaySource.label);
  expect(header).not.toContain(replaySource.label);
  expect(main).toBeDefined();
  expect(main).not.toContain(replaySource.label);
  expect(markup.split(replaySource.label)).toHaveLength(2);
}

it("renders the replay label once in the status line, outside the graph main", () => {
  expectStatusLabel(renderToStaticMarkup(<App />));
});

it("keeps the replay label in the status line while review is open", () => {
  const state = through(20);
  const view = openReview(state, initialView, "change-1");
  if (!view) throw new Error("Missing review fixture");
  expectStatusLabel(
    renderToStaticMarkup(
      <Frame
        replayLabel={replaySource.label}
        replayOf={replaySource.kind}
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
