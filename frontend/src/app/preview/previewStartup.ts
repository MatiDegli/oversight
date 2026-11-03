export interface PreviewStartupReport {
  readonly version: 1;
  readonly argv: readonly (readonly string[])[];
  readonly cwd: string;
  readonly reason: string;
  readonly dependencies: readonly string[];
  readonly limits: string;
  readonly platform_residual: string;
  readonly build_log: string;
  readonly enable_hint: string;
  readonly agents_label: string;
}

export type PreviewStartupResult =
  | { readonly kind: "valid"; readonly report: PreviewStartupReport }
  | { readonly kind: "missing" }
  | { readonly kind: "invalid" };

const keys = [
  "version",
  "argv",
  "cwd",
  "reason",
  "dependencies",
  "limits",
  "platform_residual",
  "build_log",
  "enable_hint",
  "agents_label",
] as const;
const missing = Object.freeze({ kind: "missing" } as const);
const invalid = Object.freeze({ kind: "invalid" } as const);

function wellFormed(value: unknown): value is string {
  if (typeof value !== "string") return false;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
  }
  return true;
}

function nonempty(value: unknown): value is string {
  return wellFormed(value) && value.length > 0;
}

function strings(value: unknown, min: number, max: number): value is string[] {
  return (
    Array.isArray(value) && value.length >= min && value.length <= max && value.every(nonempty)
  );
}

function isReport(value: unknown): value is PreviewStartupReport {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return (
    Object.keys(record).length === keys.length &&
    keys.every((key) => Object.hasOwn(record, key)) &&
    record.version === 1 &&
    Array.isArray(record.argv) &&
    record.argv.length >= 1 &&
    record.argv.length <= 8 &&
    record.argv.every((command: unknown) => strings(command, 1, 32)) &&
    strings(record.dependencies, 0, 16) &&
    wellFormed(record.platform_residual) &&
    ["cwd", "reason", "limits", "build_log", "enable_hint", "agents_label"].every((key) =>
      nonempty(record[key]),
    )
  );
}

/** Read only the frozen carrier; malformed or ambiguous documents fail closed. */
export function readPreviewStartup(doc: Document): PreviewStartupResult {
  try {
    const elements = doc.querySelectorAll('[id="oversight-preview-startup"]');
    if (elements.length === 0) return missing;
    const element = elements[0];
    if (
      elements.length !== 1 ||
      element?.localName !== "script" ||
      element.getAttribute("type") !== "application/json"
    )
      return invalid;
    const text = element.textContent;
    if (new TextEncoder().encode(text).byteLength > 4096) return invalid;
    const report: unknown = JSON.parse(text);
    if (!isReport(report)) return invalid;
    for (const command of report.argv) Object.freeze(command);
    Object.freeze(report.argv);
    Object.freeze(report.dependencies);
    return Object.freeze({ kind: "valid", report: Object.freeze(report) });
  } catch {
    return invalid;
  }
}

let cached: PreviewStartupResult | undefined;

/** Server startup fixes the report for this document's lifetime. */
export function previewStartup(): PreviewStartupResult {
  if (cached === undefined)
    cached = typeof document === "undefined" ? missing : readPreviewStartup(document);
  return cached;
}
