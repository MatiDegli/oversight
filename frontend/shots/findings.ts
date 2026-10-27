// The text `make shots` prints for the detector's findings, from the detect.json it saved: a count,
// then one line per finding with its rule, where it is and what it says. The scan reads as done only
// when its findings are in the log, not just a path to a file.
//
//   node shots/findings.ts <detect.json>
import { readFile } from "node:fs/promises";
import { argv } from "node:process";

interface Finding {
  antipattern?: unknown;
  severity?: unknown;
  selector?: unknown;
  snippet?: unknown;
  advisory?: unknown;
}

const text = (value: unknown, fallback: string): string =>
  typeof value === "string" && value !== "" ? value : fallback;

export function summarize(json: string): string {
  const parsed: unknown = JSON.parse(json);
  if (!Array.isArray(parsed)) throw new Error("the detector's JSON is not a list of findings");
  const findings = parsed as Finding[];
  const advisory = findings.filter((finding) => finding.advisory === true).length;
  const head =
    findings.length === 0
      ? "shots: detect found nothing"
      : `shots: detect found ${String(findings.length)} (${String(findings.length - advisory)} primary, ${String(advisory)} advisory)`;
  const lines = findings.map(
    (finding) =>
      `shots:   ${text(finding.severity, "finding")} ${text(finding.antipattern, "unnamed")}` +
      ` at ${text(finding.selector, "the page")}: ${text(finding.snippet, "no detail")}`,
  );
  return [head, ...lines].join("\n");
}

if (import.meta.main) {
  const path = argv[2];
  if (path === undefined) throw new Error("usage: findings.ts <detect.json>");
  console.log(summarize(await readFile(path, "utf8")));
}
