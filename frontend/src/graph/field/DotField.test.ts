import { beforeEach, expect, it, vi } from "vitest";

import { loadFieldController } from "./controllerLoader";

vi.mock("./controllerLoader");

beforeEach(() => {
  vi.resetModules();
  vi.mocked(loadFieldController).mockReset();
});

it("handles a failed load, retries on the next call and caches the successful preparation", async () => {
  const controller = await import("./controller");
  const load = vi.mocked(loadFieldController);
  load.mockRejectedValueOnce(new Error("Offline controller chunk"));
  load.mockResolvedValueOnce(controller);
  const { prepareDotField } = await import("./DotField");

  await expect(prepareDotField()).resolves.toBeUndefined();
  expect(load).toHaveBeenCalledTimes(1);
  await expect(prepareDotField()).resolves.toBeUndefined();
  expect(load).toHaveBeenCalledTimes(2);
  await expect(prepareDotField()).resolves.toBeUndefined();
  expect(load).toHaveBeenCalledTimes(2);
});

it("shares an in-flight preparation and keeps a successful first load cached", async () => {
  const controller = await import("./controller");
  let complete!: (module: typeof controller) => void;
  const pending = new Promise<typeof controller>((resolve) => {
    complete = resolve;
  });
  const load = vi.mocked(loadFieldController);
  load.mockReturnValueOnce(pending);
  const { prepareDotField } = await import("./DotField");

  const first = prepareDotField();
  expect(prepareDotField()).toBe(first);
  expect(prepareDotField()).toBe(first);
  expect(load).toHaveBeenCalledTimes(1);
  complete(controller);
  await expect(first).resolves.toBeUndefined();
  expect(prepareDotField()).toBe(first);
  await expect(prepareDotField()).resolves.toBeUndefined();
  expect(load).toHaveBeenCalledTimes(1);
});
