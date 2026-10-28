import type { ChosenReplaySource } from "../src/transport/index.ts";

export function scanReplaySource(bundled: ChosenReplaySource, selected: string) {
  // The detector retimes events. Every other field must still match the verified bundle
  // before its provenance label can describe the selected log.
  const withoutTimes = (text: string) =>
    text
      .trim()
      .split("\n")
      .map((line) => ({ ...(JSON.parse(line) as object), ts: null }));
  const isBundled =
    JSON.stringify(withoutTimes(selected)) === JSON.stringify(withoutTimes(bundled.text));
  return {
    text: selected,
    kind: isBundled ? bundled.kind : undefined,
    label: isBundled ? bundled.label : "",
  };
}
