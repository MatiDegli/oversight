import type { CommandError } from "../transport";

// Match the Settings tab's labels without importing its form into the start models.
const settingLabels = new Map<string, string>([
  ["META_API_KEY", "Meta API key"],
  ["BASETEN_API_KEY", "Baseten API key"],
  ["OVERSIGHT_BUDGET_USD", "Meta budget (USD)"],
  ["MUSE_SPARK_INPUT_USD_PER_M", "Muse Spark input (USD / 1M tokens)"],
  ["MUSE_SPARK_OUTPUT_USD_PER_M", "Muse Spark output (USD / 1M tokens)"],
]);
const list = new Intl.ListFormat("en", { style: "long", type: "conjunction" });

/** Explain a setup refusal using only validated setting names and problem kinds. */
export function setupFailure(error: CommandError): string {
  const missing = error.missing;
  if (!missing?.length) return error.message;
  const reasons = (["missing", "invalid"] as const).flatMap((problem) => {
    const labels = missing
      .filter((setting) => setting.problem === problem)
      .map(({ name }) => settingLabels.get(name) ?? name);
    return labels.length
      ? [`${list.format(labels)} ${labels.length === 1 ? "is" : "are"} ${problem}`]
      : [];
  });
  return `Setup is incomplete: ${reasons.join("; ")}.`;
}
