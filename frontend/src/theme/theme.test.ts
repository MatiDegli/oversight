import { readFileSync } from "node:fs";

import { compile } from "tailwindcss";
import { describe, expect, it } from "vitest";

import { agentLook, agentSlots } from "./agentLook";
import { getActiveTheme, themes } from "./index";

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");
const ts = readFileSync(new URL("./index.ts", import.meta.url), "utf8");
const design = readFileSync(new URL("../../../DESIGN.md", import.meta.url), "utf8");
const tokens = themes.dark;
// Monaco consumes these through TypeScript, so none may appear in CSS.
const tsOnly = [
  "syntax-plain",
  "syntax-keyword",
  "syntax-string",
  "syntax-number",
  "syntax-builtin",
  "syntax-definition",
  "code-dim",
  "span-highlight",
  "fold-tint",
] as const;
const radiusBlock = /@theme\s*\{([^}]+)\}/;
const rootBlock = /:root\s*\{([^}]+)\}/;
const themeBlock = /@theme inline\s*\{([^}]+)\}/;
const dayBlock = /:root\[data-theme="day"\]\s*\{([^}]+)\}/;
const darkBlock = /:root:not\(\[data-theme\]\),\s*:root\[data-theme="dark"\]\s*\{([^}]+)\}/;

function required(value: string | undefined): string {
  if (value === undefined) throw new Error("Missing token value");
  return value;
}

function declarations(block: string): Record<string, string> {
  const entries = [...block.matchAll(/--([\w*-]+)\s*:\s*([^;]+);/g)].map<[string, string]>(
    (match) => [required(match[1]), required(match[2]).trim()],
  );
  expect(new Set(entries.map(([name]) => name)).size, "No duplicate declarations").toBe(
    entries.length,
  );
  return Object.fromEntries(entries);
}

const colors = Object.entries(tokens).filter(
  ([name, value]) =>
    !tsOnly.some((token) => token === name) &&
    (typeof value === "object" || (typeof value === "string" && value.startsWith("#"))),
);
const effects = ["top-highlight", "card-shadow"];
const radii = Object.fromEntries(
  Object.entries(tokens).filter(([name]) => name.startsWith("radius-")),
);
const { "radius-*": radiusReset, ...cssRadii } = declarations(radiusBlock.exec(css)?.[1] ?? "");
const dark = declarations(darkBlock.exec(css)?.[1] ?? "");
const global = declarations(rootBlock.exec(css)?.[1] ?? "");
const shared = { ...dark, ...global, ...cssRadii };

// Preserve alpha as a number instead of baking it into an eight-digit hex string.
function rgba(red: string, green: string, blue: string, alpha: string) {
  return {
    color: `#${[red, green, blue].map((part) => Number(part).toString(16).padStart(2, "0")).join("")}`,
    alpha: Number(alpha),
  };
}

function cssValue(value: string, values = shared): unknown {
  const alias = /^var\(--([\w-]+)\)$/.exec(value);
  if (alias) return cssValue(required(values[required(alias[1])]), values);
  const mix = /^color-mix\(in srgb, var\(--([\w-]+)\) ([\d.]+)%, transparent\)$/.exec(value);
  if (mix)
    return {
      color: cssValue(required(values[required(mix[1])]), values),
      alpha: Number(mix[2]) / 100,
    };
  const channels = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)$/.exec(value);
  if (channels)
    return rgba(
      required(channels[1]),
      required(channels[2]),
      required(channels[3]),
      required(channels[4]),
    );
  return /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : value;
}

describe("theme", () => {
  it("keeps every shared CSS token equal to its TypeScript value, including tint alpha", () => {
    expect(Object.keys(shared).length).toBeGreaterThan(0);
    const expected = Object.fromEntries(
      Object.entries(tokens).filter(([name]) => !tsOnly.some((token) => token === name)),
    );
    // Whole-object equality rejects missing counterparts in either direction.
    expect(
      Object.fromEntries(Object.entries(shared).map(([key, value]) => [key, cssValue(value)])),
    ).toEqual(expected);
    expect(
      declarations(
        css
          .replace(darkBlock, "")
          .replace(dayBlock, "")
          .replace(rootBlock, "")
          .replace(radiusBlock, "")
          .replace(themeBlock, ""),
      ),
    ).toEqual({});
    for (const name of tsOnly) expect(css).not.toContain(`--${name}:`);
  });

  it("maps every shared color to a Tailwind utility and removes the default palette", () => {
    const block = themeBlock.exec(css)?.[1] ?? "";
    expect(declarations(block)).toEqual({
      "color-*": "initial",
      "font-*": "initial",
      ...Object.fromEntries(colors.map(([name]) => [`color-${name}`, `var(--${name})`])),
      "font-sans": "var(--text-font)",
      "font-mono": "var(--code-font)",
    });
  });

  it("registers design radii with Tailwind and removes default radius utilities", async () => {
    // Use the installed defaults so a missing namespace reset cannot pass unnoticed.
    const defaults = readFileSync(new URL(import.meta.resolve("tailwindcss/theme.css")), "utf8");
    const compiler = await compile(
      `${defaults}\n${css.replace(/^@import .*;$/gm, "")}\n@tailwind utilities;`,
    );
    const output = compiler.build([
      ...Object.keys(radii).map((name) => name.replace("radius-", "rounded-")),
      "rounded-lg",
    ]);
    for (const name of Object.keys(radii)) {
      const utility = name.replace("radius-", "rounded-");
      expect(output).toContain(`.${utility} {\n  border-radius: var(--${name});\n}`);
    }
    expect(output).not.toContain(".rounded-lg {");
    expect(radiusReset).toBe("initial");
    expect(cssRadii).toEqual(radii);
  });

  it("scopes only colors to dark and keeps all theme-independent tokens global", () => {
    expect(Object.keys(dark).sort()).toEqual([...colors.map(([name]) => name), ...effects].sort());
    expect(global).toEqual(
      Object.fromEntries(
        Object.entries(tokens)
          .filter(
            ([name]) =>
              !tsOnly.some((token) => token === name) &&
              !colors.some(([color]) => color === name) &&
              !effects.includes(name) &&
              !name.startsWith("radius-"),
          )
          .map(([name, value]) => [name, typeof value === "number" ? String(value) : value]),
      ),
    );
  });

  it("documents failed-text for destructive labels and icons", () => {
    for (const source of [css, ts]) {
      expect(source.match(/Destructive text and icons.*failed-text\./)?.[0]).toBe(
        "Destructive text and icons (Reject, Stop) use failed-text.",
      );
    }
    expect(cssValue(required(dark["failed-text"]))).toBe(tokens["failed-text"]);
    expect(tokens["failed-text"]).toBe("#ff7b7b");
  });

  it("includes the complete DESIGN.md dark color set with its specified values", () => {
    const front = required(required(design.split("---")[1]).split("typography:")[0]);
    const entries = [...front.matchAll(/^ {2}([\w-]+): "([^"\n]+)"$/gm)].filter(
      (match) => !required(match[1]).startsWith("day-"),
    );
    expect(entries.length).toBeGreaterThan(0);
    // DECISIONS.md: Shell-frame docs front (DESIGN.md parity) removes these on landing.
    const approved: Record<string, string> = {
      shell: "#09090a",
      panel: "#0d0d0f",
      page: "#101012",
      card: "#151518",
      raised: "#1b1b1f",
      divider: "rgba(255, 255, 255, 0.06)",
      border: "rgba(255, 255, 255, 0.1)",
    };
    for (const [, name, value] of entries) {
      if (approved[required(name)])
        expect(value, `Remove the landed DESIGN.md override for ${required(name)}`).not.toBe(
          approved[required(name)],
        );
      expect(tokens[name as keyof typeof tokens], name).toEqual(
        cssValue(approved[required(name)] ?? required(value)),
      );
    }
    expect(tokens["dim-node"]).toBe(0.16);
    expect(tokens["dim-module"]).toBe(0.6);
    expect(tokens["quiet-edge"]).toBe(0.45);
  });

  it("removes the obsolete pre-M8 overlay opacity tokens from both token sets", () => {
    for (const name of ["shell-opacity", "cone-card-opacity"]) {
      expect(tokens).not.toHaveProperty(name);
      expect(shared).not.toHaveProperty(name);
    }
  });

  it("ties temporary DESIGN.md overrides to the shell-frame docs front", () => {
    const decisions = readFileSync(new URL("../../../DECISIONS.md", import.meta.url), "utf8");
    const note = required(
      decisions.split("Approved shell frame and Chrome close-tab key")[1],
    ).split("At 1920 px")[0];
    expect(note).toContain("Shell-frame docs front (DESIGN.md parity)");
    expect(note).toContain("remove the seven-token `approved` override");
    expect(note).toContain("frontend/src/theme/theme.test.ts");
  });

  it("loads the documented font weights from the installed packages", () => {
    for (const weight of [400, 600, 700]) {
      expect(css).toContain(
        `@import "@fontsource/atkinson-hyperlegible-next/latin-${String(weight)}.css";`,
      );
    }
    expect(css).toContain('@import "@fontsource/atkinson-hyperlegible-mono/latin-400.css";');
  });

  it("selects the root theme at call time and defaults to dark without a DOM", () => {
    expect(Object.keys(themes)).toEqual(["dark", "day"]);
    expect(getActiveTheme()).toBe(themes.dark);
    const root = { dataset: {} } as HTMLElement;
    expect(getActiveTheme(root)).toBe(themes.dark);
    root.dataset.theme = "dark";
    expect(getActiveTheme(root)).toBe(themes.dark);
    root.dataset.theme = "day";
    expect(getActiveTheme(root)).toBe(themes.day);
    root.dataset.theme = "unknown";
    expect(() => getActiveTheme(root)).toThrow("Unsupported theme: unknown");
    root.dataset.theme = "dark";
    expect(getActiveTheme(root)).toBe(themes.dark);
  });
});

// WCAG 2.x relative luminance, using the lighter gradient end as well as its dark end.
function luminance(hex: string): number {
  const channels = [1, 3, 5].map(
    (offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255,
  );
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return (linear[0] ?? 0) * 0.2126 + (linear[1] ?? 0) * 0.7152 + (linear[2] ?? 0) * 0.0722;
}
function contrast(a: string, b: string) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05);
}
const surfaces = [
  "shell",
  "panel",
  "field",
  "page",
  "page-raised",
  "card",
  "card-end",
  "raised",
  "inset",
  "matte-top",
  "matte-bottom",
] as const;
const bodyText = [
  "text",
  "muted",
  "faint",
  "agent-a-text",
  "agent-b-text",
  "agent-c-text",
  "agent-d-text",
  "agent-e-text",
  "agent-f-text",
  "passed",
  "failed",
  "failed-text",
] as const;
it.each(surfaces)("meets body contrast for every shell text token on %s", (surface) => {
  for (const text of bodyText)
    expect(contrast(tokens[text], tokens[surface]), `${text} on ${surface}`).toBeGreaterThanOrEqual(
      4.5,
    );
  for (const mark of [
    ...agentSlots.map((slot) => agentLook[slot].family),
    "text",
    "passed",
    "failed",
  ] as const)
    expect(
      contrast(tokens[mark], tokens[surface]),
      `${mark} UI mark on ${surface}`,
    ).toBeGreaterThanOrEqual(3);
});

it.each([
  ["module-label", "ground"],
  ["text-hover", "raised"],
] as const)("meets body contrast for %s on %s", (text, surface) => {
  expect(contrast(tokens[text], tokens[surface])).toBeGreaterThanOrEqual(4.5);
});

// CSS alpha compositing happens in sRGB before relative luminance is calculated.
function composite(foreground: { color: string; alpha: number }, background: string): string {
  return `#${[1, 3, 5]
    .map((offset) => {
      const front = Number.parseInt(foreground.color.slice(offset, offset + 2), 16);
      const back = Number.parseInt(background.slice(offset, offset + 2), 16);
      return Math.round(front * foreground.alpha + back * (1 - foreground.alpha))
        .toString(16)
        .padStart(2, "0");
    })
    .join("")}`;
}

it.each(agentSlots.map((slot) => agentLook[slot].family))(
  "meets body contrast for %s text on its tint and letters on its fill",
  (agent) => {
    // Card badges include both gradient ends; the collapsed agent rail uses shell.
    for (const surface of ["shell", "card", "card-end", "matte-top"] as const) {
      const background = composite(tokens[`${agent}-tint`], tokens[surface]);
      expect(
        contrast(tokens[`${agent}-text`], background),
        `${agent}-text on ${agent}-tint over ${surface}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
    expect(
      contrast(tokens["on-agent"], tokens[agent]),
      `on-agent on ${agent}`,
    ).toBeGreaterThanOrEqual(4.5);
  },
);

it.each(["text", "muted"] as const)("meets body contrast for %s on frosted overlays", (text) => {
  // Bound any blurred graph content, including agent lights, between black and white.
  for (const backdrop of ["#000000", "#ffffff"]) {
    const background = composite(tokens["overlay-panel"], backdrop);
    expect(
      contrast(tokens[text], background),
      `${text} on overlay-panel over ${backdrop}`,
    ).toBeGreaterThanOrEqual(4.5);
  }
});

it("checks alpha compositing against reference colors", () => {
  expect(composite({ color: "#ffffff", alpha: 0.5 }, "#000000")).toBe("#808080");
  expect(composite({ color: "#ff0000", alpha: 0 }, "#123456")).toBe("#123456");
  expect(composite({ color: "#ff0000", alpha: 1 }, "#123456")).toBe("#ff0000");
});
it.each(["text", "muted", "faint"] as const)(
  "meets body contrast for %s on M8 overlay fills",
  (text) => {
    expect(tokens["overlay-fill"].alpha).toBeGreaterThanOrEqual(0.94);
    for (const backdrop of ["#000000", "#ffffff"])
      expect(
        contrast(tokens[text], composite(tokens["overlay-fill"], backdrop)),
      ).toBeGreaterThanOrEqual(4.5);
  },
);
it("checks WCAG reference pairs so a broken calculation cannot pass", () => {
  expect(contrast("#000000", "#ffffff")).toBe(21);
  expect(contrast("#777777", "#ffffff")).toBeLessThan(4.5);
});

it("retires unused per-mode emitter weights from TypeScript and CSS", () => {
  for (const name of [
    "watch-changed-weight",
    "watch-read-weight",
    "review-changed-weight",
    "review-new-weight",
    "review-cone-near-weight",
    "review-cone-far-weight",
    "live-diff-editing-weight",
    "live-diff-cone-weight",
    "file-view-selected-weight",
    "file-view-neighbor-weight",
  ]) {
    expect(tokens).not.toHaveProperty(name);
    expect(shared).not.toHaveProperty(name);
  }
  expect(tokens["watch-editing-weight"]).toBe(1);
  expect(tokens["live-diff-read-weight"]).toBe(0.4);
});

it("keeps the prepared day CSS, TypeScript and DESIGN palette in sync", () => {
  const values = declarations(dayBlock.exec(css)?.[1] ?? "");
  const active = { ...shared, ...values };
  const expected = Object.fromEntries(
    Object.entries(themes.day).filter(([name]) => !tsOnly.some((token) => token === name)),
  );
  expect(
    Object.fromEntries(
      Object.entries(active).map(([key, value]) => [key, cssValue(value, active)]),
    ),
  ).toEqual(expected);
  const front = required(design.split("---")[1]);
  for (const [, name, value] of front.matchAll(/^ {2}day-([\w-]+): "([^"\n]+)"$/gm)) {
    const actual = themes.day[required(name) as keyof typeof themes.day];
    const normalized = typeof actual === "object" && actual.alpha === 1 ? actual.color : actual;
    expect(normalized, name).toEqual(cssValue(required(value), active));
  }
});

it.each(agentSlots)(
  "checks %s graphic, text, tinted text and filled letter in both palettes",
  (slot) => {
    const { family, text } = agentLook[slot];
    for (const [name, palette] of Object.entries(themes)) {
      const backgrounds =
        name === "dark"
          ? ["#101012", "#151518", "#131313"]
          : ["#f8f7f3", "#fcfbf8", "#fbfaf7", "#f4f3ef", "#eeede8"];
      for (const background of backgrounds) {
        expect(
          contrast(palette[family], background),
          `${name} ${slot} graphic on ${background}`,
        ).toBeGreaterThanOrEqual(3);
        expect(
          contrast(palette[text], background),
          `${name} ${slot} text on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
      for (const surface of ["shell", "card", "card-end", "matte-top"] as const)
        expect(
          contrast(palette[text], composite(palette[`${family}-tint`], palette[surface])),
          `${name} ${slot} tint on ${surface}`,
        ).toBeGreaterThanOrEqual(4.5);
      for (const suffix of ["-node", "-node-new"] as const)
        expect(
          contrast(palette[text], composite(palette[`${family}${suffix}`], palette.ground)),
          `${name} ${slot} ${suffix}`,
        ).toBeGreaterThanOrEqual(4.5);
      expect(
        contrast(palette["on-agent"], palette[family]),
        `${name} ${slot} letter on fill`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  },
);

// D/E keep their approved text: the backing must supply contrast on darker day surfaces.
it.each(["D", "E"] as const)("keeps day %s tint text readable across shell surfaces", (slot) => {
  const { family, text } = agentLook[slot];
  for (const surface of surfaces) {
    const background = composite(themes.day[`${family}-tint`], themes.day[surface]);
    expect(
      contrast(themes.day[text], background),
      `${slot} tint over ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
  }
  for (const suffix of ["-node", "-node-new"] as const)
    expect(
      contrast(themes.day[text], themes.day[`${family}${suffix}`].color),
    ).toBeGreaterThanOrEqual(4.5);
});
