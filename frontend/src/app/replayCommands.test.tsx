import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import type { FileAccess } from "../editor";
import type { FrameProps } from "./Frame";
import { App } from "./index";

// The static replay site has no backend: the app must not even create the command client there.
const transport = vi.hoisted(() => ({ replayBuild: true, createCommandClient: vi.fn() }));
const frame = vi.hoisted(() => ({ files: undefined as FileAccess | undefined }));
vi.mock("./Frame", () => ({
  Frame: (props: FrameProps) => {
    frame.files = props.files;
    return null;
  },
}));
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
  expect(frame.files).toBeUndefined();
  transport.replayBuild = false;
  const getFile = vi.fn();
  const saveFile = vi.fn();
  transport.createCommandClient.mockReturnValue({ getFile, saveFile });
  renderToStaticMarkup(<App />);
  expect(transport.createCommandClient).toHaveBeenCalledTimes(1);
  expect(frame.files?.readFile).toBe(getFile);
  expect(frame.files?.drafts.size).toBe(0);
  expect(frame.files?.inFlightSaves.size).toBe(0);
  expect(frame.files?.saveFile).toBeTypeOf("function");
});
