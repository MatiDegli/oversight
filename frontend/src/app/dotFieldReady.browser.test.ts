import { expect, it } from "vitest";

import { lifeOf, withPage } from "./dotField.browser.support";

it("waits for the lazy field controller even when the graph is ready first", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce", slowField: true }, async (page) => {
    expect(await lifeOf(page)).toEqual({ pulses: 0, lights: 0, contours: true });
  });
}, 60_000);
