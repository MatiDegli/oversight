import { afterEach, expect, it, vi } from "vitest";
import { readPreviewStartup, type PreviewStartupReport } from "./previewStartup";

const report: PreviewStartupReport = {
  version: 1,
  argv: [
    ["pnpm", "build"],
    ["pnpm", "start", "--port", "8123"],
  ],
  cwd: "/workspace/应用/é🙂",
  reason: "Selected package scripts",
  dependencies: ["pnpm install --offline"],
  limits: "Memory: 256 MiB; lifetime: 300 s",
  platform_residual: "",
  build_log: "/runtime/build.log",
  enable_hint: "Set OVERSIGHT_PREVIEW_ENABLED=1",
  agents_label: "Agent A · candidate",
};
function element(text: string, tag = "script", type = "application/json") {
  return { localName: tag, getAttribute: vi.fn(() => type), textContent: text };
}
function documentWith(...elements: ReturnType<typeof element>[]) {
  return { querySelectorAll: vi.fn(() => elements) } as unknown as Document;
}
const read = (value: unknown) => readPreviewStartup(documentWith(element(JSON.stringify(value))));
afterEach(() => vi.unstubAllGlobals());

it("preserves escaped hostile strings, multibyte paths and an empty residual exactly", () => {
  const hostile = '</script><!-- " \\ & \u2028\u2029';
  const expected = { ...report, argv: [[hostile]], reason: hostile };
  const text = JSON.stringify(expected).replace(
    /[<>&\u2028\u2029]/gu,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
  expect(readPreviewStartup(documentWith(element(text)))).toEqual({
    kind: "valid",
    report: expected,
  });
});

it("freezes the report, both array levels and dependencies", () => {
  const result = read(report);
  expect(result.kind).toBe("valid");
  if (result.kind !== "valid") throw new Error("Expected a valid report");
  for (const value of [result, result.report, result.report.argv, result.report.dependencies])
    expect(Object.isFrozen(value)).toBe(true);
  expect(result.report.argv.every(Object.isFrozen)).toBe(true);
});

it("distinguishes missing from wrong or duplicate elements without reading other content", () => {
  expect(readPreviewStartup(documentWith())).toEqual({ kind: "missing" });
  const text = JSON.stringify(report);
  for (const elements of [
    [element(text, "div")],
    [element(text, "script", "text/json")],
    [element(text, "script", "application/json ")],
    [element(text), element(text)],
    [element(text), element(text, "div")],
  ])
    expect(readPreviewStartup(documentWith(...elements))).toEqual({ kind: "invalid" });
});

it.each(["", "{", "null", "[]", '"text"', "1", "true"])(
  "rejects invalid JSON or non-object %j",
  (text) => {
    expect(readPreviewStartup(documentWith(element(text)))).toEqual({ kind: "invalid" });
  },
);

it.each(Object.keys(report))("requires the exact key %s and rejects null", (key) => {
  const missing = Object.fromEntries(Object.entries(report).filter(([name]) => name !== key));
  expect(read(missing)).toEqual({ kind: "invalid" });
  expect(read({ ...report, [key]: null })).toEqual({ kind: "invalid" });
});

it("rejects extra keys including prototype-looking keys", () => {
  expect(read({ ...report, extra: true })).toEqual({ kind: "invalid" });
  expect(read({ ...report, ["__proto__"]: {} })).toEqual({ kind: "invalid" });
});

it.each([0, 2, 1.1, "1", true, {}, []])("rejects version %j", (version) => {
  expect(read({ ...report, version })).toEqual({ kind: "invalid" });
});

it.each(["cwd", "reason", "limits", "build_log", "enable_hint", "agents_label"])(
  "requires a nonempty well-formed string for %s, preserving whitespace",
  (key) => {
    for (const value of ["", 1, false, [], {}, "\ud800", "\udc00", "a\ud800b"])
      expect(read({ ...report, [key]: value })).toEqual({ kind: "invalid" });
    expect(read({ ...report, [key]: " \n\t " }).kind).toBe("valid");
  },
);

it.each([0, false, [], {}, "\ud800", "\udc00"])("rejects residual %j", (value) => {
  expect(read({ ...report, platform_residual: value })).toEqual({ kind: "invalid" });
});

it.each([
  [],
  Array.from({ length: 9 }, () => ["x"]),
  [[]],
  [Array.from({ length: 33 }, () => "x")],
  ["x"],
  [[""]],
  [[1]],
  [[null]],
  [["\ud800"]],
  {},
  "x",
])("rejects argv shape/count %j", (argv) => {
  expect(read({ ...report, argv })).toEqual({ kind: "invalid" });
});

it.each([Array(17).fill("x"), [""], [1], [null], ["\udc00"], {}, "x"])(
  "rejects dependencies shape/count %j",
  (dependencies) => {
    expect(read({ ...report, dependencies })).toEqual({ kind: "invalid" });
  },
);

it("accepts inclusive command, argument and dependency count bounds", () => {
  for (const argv of [[["x"]], Array.from({ length: 8 }, () => ["x"]), [Array(32).fill("x")]])
    for (const dependencies of [[], Array(16).fill("x")])
      expect(read({ ...report, argv, dependencies }).kind).toBe("valid");
});

it("measures delivered UTF-8 bytes before parsing, without trimming or truncating", () => {
  const base = JSON.stringify({ ...report, reason: "" });
  const bytes = new TextEncoder().encode(base).byteLength;
  const text = JSON.stringify({ ...report, reason: "é" + "x".repeat(1024 - bytes - 2) });
  expect(new TextEncoder().encode(text)).toHaveLength(1024);
  expect(readPreviewStartup(documentWith(element(text))).kind).toBe("valid");
  expect(readPreviewStartup(documentWith(element(text + " ")))).toEqual({ kind: "invalid" });
  expect(readPreviewStartup(documentWith(element(" " + text)))).toEqual({ kind: "invalid" });
});

it("rejects compact JSON whose carrier escapes exceed the byte limit", () => {
  const text = JSON.stringify({ ...report, reason: "<".repeat(120) });
  expect(new TextEncoder().encode(text).byteLength).toBeLessThan(1024);
  const escaped = text.replaceAll("<", "\\u003c");
  expect(new TextEncoder().encode(escaped).byteLength).toBeGreaterThan(1024);
  expect(readPreviewStartup(documentWith(element(escaped)))).toEqual({ kind: "invalid" });
});

it("never throws on a document read failure", () => {
  const doc = {
    querySelectorAll: () => {
      throw new Error("unreadable");
    },
  };
  expect(readPreviewStartup(doc as unknown as Document)).toEqual({ kind: "invalid" });
});

it("memoizes the first document read even when its content changes", async () => {
  vi.resetModules();
  const querySelectorAll = vi.fn(() => [element(JSON.stringify(report))]);
  vi.stubGlobal("document", { querySelectorAll });
  const { previewStartup } = await import("./previewStartup");
  const first = previewStartup();
  querySelectorAll.mockReturnValue([]);
  expect(previewStartup()).toBe(first);
  expect(querySelectorAll).toHaveBeenCalledExactlyOnceWith('[id="oversight-preview-startup"]');
});
