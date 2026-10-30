import { expect, it, vi } from "vitest";

import { browserLaunchOptions } from "./browserLaunch.support";

it.each([false, true])("only selects software rendering with WebGL enabled: %s", (webGL) => {
  vi.stubEnv("OVERSIGHT_E2E_SOFTWARE_RENDERING", "1");
  try {
    expect(browserLaunchOptions({ webGL, args: ["--no-sandbox"], headless: true })).toEqual({
      headless: true,
      args: [
        "--no-sandbox",
        ...(webGL
          ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
          : ["--disable-webgl"]),
      ],
    });
    expect(browserLaunchOptions()).toEqual({ args: ["--disable-webgl"] });
    expect(browserLaunchOptions({ webGL, softwareRendering: false })).toEqual({
      args: webGL ? [] : ["--disable-webgl"],
    });
  } finally {
    vi.unstubAllEnvs();
  }
});

it("allows field tests to request software rendering without the environment variable", () => {
  vi.stubEnv("OVERSIGHT_E2E_SOFTWARE_RENDERING", "0");
  try {
    expect(browserLaunchOptions({ webGL: true })).toEqual({ args: [] });
    expect(browserLaunchOptions({ webGL: true, softwareRendering: true })).toEqual({
      args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    });
  } finally {
    vi.unstubAllEnvs();
  }
});
