import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import { App } from "./index";

// The static replay site has no backend: the app must not even create the command client there.
const transport = vi.hoisted(() => ({ replayBuild: true, createCommandClient: vi.fn() }));
vi.mock("../transport", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../transport")>()),
  get replayBuild() {
    return transport.replayBuild;
  },
  createCommandClient: transport.createCommandClient,
}));
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

it("creates the command client in a live build and never in the replay build", () => {
  transport.replayBuild = true;
  renderToStaticMarkup(<App />);
  expect(transport.createCommandClient).not.toHaveBeenCalled();
  transport.replayBuild = false;
  renderToStaticMarkup(<App />);
  expect(transport.createCommandClient).toHaveBeenCalledTimes(1);
});
