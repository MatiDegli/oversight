import type { UsageView } from "@contracts";

/** Read and error decoding stay in the deferred Usage module. */
export async function readUsage(
  runId: string,
  signal: AbortSignal,
  // The approved Usage brief explicitly owns this deferred read outside transport.
  // eslint-disable-next-line no-restricted-globals
  request: (url: string, init: RequestInit) => Promise<Response> = fetch,
): Promise<UsageView> {
  let response: Response;
  try {
    response = await request("/usage", { signal });
  } catch {
    throw new Error("Usage did not reach the server. Check the connection and try again.");
  }
  if (!response.ok) {
    let message = `HTTP ${String(response.status)} ${response.statusText}`.trim();
    try {
      const body = (await response.json()) as { error?: { message?: unknown } };
      if (typeof body.error?.message === "string" && body.error.message)
        message = body.error.message;
    } catch {
      /* The proxy may return HTML or an empty body. */
    }
    throw new Error(`Usage could not be read: ${message}`);
  }
  const data = (await response.json()) as UsageView;
  if (data.run_id !== runId)
    throw new Error("Usage belongs to another run. Try again for the current run.");
  return data;
}
