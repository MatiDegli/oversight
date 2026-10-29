import type { Page } from "@playwright/test";

export async function withCpuThrottling(page: Page, rate: number, run: () => Promise<void>) {
  const session = await page.context().newCDPSession(page);
  try {
    await session.send("Emulation.setCPUThrottlingRate", { rate });
    await run();
  } finally {
    // Reset before closing the page: Chromium can reuse its renderer for later cases.
    try {
      await session.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    } finally {
      await session.detach();
    }
  }
}
