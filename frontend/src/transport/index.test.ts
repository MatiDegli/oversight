import { afterEach, beforeEach, expect, it, vi } from "vitest";

const recording = vi.hoisted(() => ({ text: undefined as string | undefined }));
vi.mock("../../../fixtures/events/recorded-demo.jsonl?recorded-replay", async (original) => {
  const { default: verified } =
    await original<typeof import("../../../fixtures/events/recorded-demo.jsonl?recorded-replay")>();
  return {
    default: {
      ...verified,
      get text() {
        return recording.text ?? verified.text;
      },
    },
  };
});

beforeEach(() => {
  vi.resetModules();
  recording.text = undefined;
});
afterEach(() => vi.unstubAllEnvs());

it.each(["", " ", "\n\t\r\n"])("refuses a blank recording in a replay build (%j)", async (text) => {
  recording.text = text;
  vi.stubEnv("MODE", "replay");
  await expect(import("./index")).rejects.toThrow("Replay build requires a non-empty recording");
});

it("keeps the verified recording in a replay build", async () => {
  vi.stubEnv("MODE", "replay");
  const transport = await import("./index");
  expect(transport.replayBuild).toBe(true);
  expect(transport.replaySource.kind).toBe("real recorded run");
  expect(transport.replaySource.text).toBe(transport.replayRecording.text);
});

it.each(["development", "production"])("keeps the fixture fallback in %s", async (mode) => {
  recording.text = "";
  vi.stubEnv("MODE", mode);
  const transport = await import("./index");
  expect(transport.replayBuild).toBe(false);
  expect(transport.replaySource.kind).toBe("hand-written demo fixture");
  expect(transport.replaySource.text.trim()).not.toBe("");
  expect(transport.chooseReplaySource(null, "own fixture").text).toBe("own fixture");
});
