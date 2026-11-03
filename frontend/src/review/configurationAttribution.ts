// The backend stores the proposal's configuration diagnostic in captured facts problems under
// this stable prefix (front backend/config-diag, built in parallel; until it lands no real
// recording carries it).
export const CONFIGURATION_ATTRIBUTION_PREFIX = "configuration attribution: ";

export function partitionConfigurationAttribution(problems: readonly string[] = []) {
  const attributions: string[] = [];
  const ordinary: string[] = [];
  for (const problem of problems) {
    (problem.startsWith(CONFIGURATION_ATTRIBUTION_PREFIX) ? attributions : ordinary).push(problem);
  }
  return { attributions, problems: ordinary };
}
