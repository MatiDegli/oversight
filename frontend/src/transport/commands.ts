import type {
  AgentCreated,
  AgentMessage,
  ApproveChange,
  ErrorCode,
  FileContent,
  FileSaved,
  GraphResponse,
  Health,
  ImpactCone,
  RejectChange,
  RequestChanges,
  SaveFile,
  StartAgent,
} from "@contracts";

// Keep unknown HTTP failures separate from the contract's application error codes.
const errorCodes: Record<ErrorCode, true> = {
  not_found: true,
  wrong_state: true,
  slot_busy: true,
  spend_brake: true,
  invalid_path: true,
  stale_file: true,
  not_implemented: true,
  invalid_request: true,
  internal: true,
};

export class CommandError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: ErrorCode | undefined,
    message: string,
  ) {
    super(message);
    this.name = "CommandError";
  }
}

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return `${path}?${params}`;
}

export function createCommandClient(request: typeof fetch = fetch) {
  async function send<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal) {
    const response = await request(path, {
      method,
      signal,
      ...(body === undefined
        ? {}
        : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      let code: ErrorCode | undefined;
      let message = `HTTP ${String(response.status)} ${response.statusText}`.trim();
      try {
        const body: unknown = await response.json();
        if (typeof body === "object" && body !== null && "error" in body) {
          const error = body.error;
          if (
            typeof error === "object" &&
            error !== null &&
            "message" in error &&
            typeof error.message === "string" &&
            error.message !== ""
          ) {
            // The server's words are kept even when its code is not one the contract names.
            message = error.message;
            if (
              "code" in error &&
              typeof error.code === "string" &&
              Object.hasOwn(errorCodes, error.code)
            )
              code = error.code as ErrorCode;
          }
        }
      } catch {
        // Proxies and servers may answer with HTML, plain text, or an empty body.
      }
      throw new CommandError(response.status, code, message);
    }
    // Accepted commands have no result body; their results arrive through SSE.
    if (response.status === 202 || response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  const agent = (id: string, action: string) => `/agents/${encodeURIComponent(id)}/${action}`;
  const change = (id: string, action: string) => `/changes/${encodeURIComponent(id)}/${action}`;
  return {
    startAgent: (body: StartAgent) => send<AgentCreated>("/agents", "POST", body),
    stopAgent: (id: string) => send<undefined>(agent(id, "stop"), "POST"),
    messageAgent: (id: string, body: AgentMessage) =>
      send<undefined>(agent(id, "message"), "POST", body),
    pauseAgent: (id: string) => send<undefined>(agent(id, "pause"), "POST"),
    resumeAgent: (id: string) => send<undefined>(agent(id, "resume"), "POST"),
    approveChange: (id: string, body: ApproveChange = {}) =>
      send<undefined>(change(id, "approve"), "POST", body),
    requestChanges: (id: string, body: RequestChanges) =>
      send<undefined>(change(id, "request-changes"), "POST", body),
    rejectChange: (id: string, body: RejectChange = {}) =>
      send<undefined>(change(id, "reject"), "POST", body),
    getGraph: (signal?: AbortSignal) => send<GraphResponse>("/graph", "GET", undefined, signal),
    rescanGraph: () => send<undefined>("/graph/rescan", "POST"),
    getImpact: (node: string, depth?: number, change?: string) =>
      send<ImpactCone>(query("/impact", { node, depth, change })),
    getFile: (path: string, agent?: string) => send<FileContent>(query("/files", { path, agent })),
    saveFile: (body: SaveFile) => send<FileSaved>("/files", "PUT", body),
    getHealth: () => send<Health>("/health"),
  };
}
