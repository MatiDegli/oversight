// Test infrastructure only; product code must never import this launcher.
import { chromium } from "@playwright/test";
import type { LaunchOptions } from "@playwright/test";

type BrowserOptions = LaunchOptions & { webGL?: boolean; softwareRendering?: boolean };

/** Use the product's plain-ground fallback unless a test checks the field itself. */
export function browserLaunchOptions({
  webGL = false,
  softwareRendering = process.env.OVERSIGHT_E2E_SOFTWARE_RENDERING === "1",
  ...options
}: BrowserOptions = {}): LaunchOptions {
  return {
    ...options,
    args: [
      ...(options.args ?? []),
      ...(webGL ? [] : ["--disable-webgl"]),
      ...(softwareRendering ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : []),
    ],
  };
}

export function launchBrowser(options: BrowserOptions = {}) {
  return chromium.launch(browserLaunchOptions(options));
}
