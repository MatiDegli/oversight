import type { PreviewReason } from "./previewTypes";

export const previewReasonCopy = {
  closed: "The preview was closed.",
  lease_expired: "Preview ownership expired. Open it again explicitly to continue.",
  lifetime_expired: "The preview’s time limit ended. Open it again explicitly to continue.",
  mutation:
    "A repository operation closed the preview. Inspect the current candidate before reopening.",
  shutdown: "Oversight is shutting down.",
  app_exited: "The app exited.",
  readiness_timeout: "The app did not become ready in time.",
  launch_failed: "The app could not start.",
  isolation_unavailable: "Live preview unavailable: isolation C is unavailable for this session.",
  memory_limit: "The app reached its memory limit.",
  cleanup_unverified:
    "Closure could not be verified. The preview remains unavailable for replacement.",
  worktree_changed: "The worktree changed while previewing. Inspect the current candidate.",
  profile_disabled: "Live preview was disabled for the session.",
} satisfies Record<PreviewReason, string>;
