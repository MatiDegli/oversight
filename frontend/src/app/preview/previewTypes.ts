import type { PreviewState, PreviewReason, PreviewStatus, PreviewDetails } from "@contracts";
export type { PreviewState, PreviewReason, PreviewStatus, PreviewDetails } from "@contracts";

export const previewStates = [
  "starting",
  "ready",
  "stopping",
  "stopped",
  "failed",
] as const satisfies readonly PreviewState[];
export const previewReasons = [
  "closed",
  "lease_expired",
  "lifetime_expired",
  "mutation",
  "shutdown",
  "app_exited",
  "readiness_timeout",
  "launch_failed",
  "isolation_unavailable",
  "memory_limit",
  "cleanup_unverified",
  "worktree_changed",
  "profile_disabled",
] as const satisfies readonly PreviewReason[];

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid preview input");
  const object = value as Record<string, unknown>;
  if (Object.keys(object).length !== keys.length || keys.some((key) => !Object.hasOwn(object, key)))
    throw new Error("Invalid preview keys");
  return object;
}
function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
export function previewId(value: unknown): value is string {
  return typeof value === "string" && /^preview-[0-9a-f]{32}$/.test(value);
}
/** Generated interfaces are not validators. Validate the injected boundary, including nulls. */
export function parsePreviewStatus(value: unknown): PreviewStatus {
  const data = record(value, [
    "preview_id",
    "run_id",
    "agent_id",
    "change_id",
    "revision",
    "head_commit",
    "state",
    "reason",
  ]);
  if (
    !previewId(data.preview_id) ||
    ![data.run_id, data.agent_id, data.change_id, data.head_commit].every(text) ||
    !Number.isSafeInteger(data.revision) ||
    Number(data.revision) <= 0 ||
    !previewStates.some((state) => state === data.state)
  )
    throw new Error("Invalid preview status");
  const active = data.state === "starting" || data.state === "ready";
  if (active ? data.reason !== null : !previewReasons.some((reason) => reason === data.reason))
    throw new Error("Invalid preview reason");
  return Object.freeze({ ...data }) as unknown as PreviewStatus;
}
export function parsePreviewDetails(value: unknown): PreviewDetails {
  const data = record(value, ["status", "url"]);
  const status = parsePreviewStatus(data.status);
  if (data.url !== null && (!text(data.url) || status.state !== "ready"))
    throw new Error("Invalid preview URL");
  return Object.freeze({ status, url: data.url });
}
