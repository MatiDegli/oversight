import { createLivePreviewPort, type PreviewValidators } from "./preview";
/** Acceptance keeps its explicit trusted backend destination and production validation. */
export function createPreviewAcceptancePort(
  base: string,
  validators: PreviewValidators,
  request: typeof fetch = fetch,
) {
  return createLivePreviewPort(base, validators, request);
}
