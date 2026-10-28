/** Main and parallel worktrees must never share a draft or an in-flight save. */
export const fileKey = (path: string, agentId?: string | null) =>
  agentId == null ? path : JSON.stringify([agentId, path]);
