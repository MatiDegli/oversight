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
    let message = "The usage service is unavailable. Try again.";
    try {
      const body = (await response.json()) as { error?: { message?: unknown } };
      if (typeof body.error?.message === "string" && body.error.message)
        message = `${body.error.message} Try again.`;
    } catch {
      /* The proxy may return HTML or an empty body. */
    }
    throw new Error(`Usage could not be read: ${message}`);
  }
  let data: UsageView;
  try {
    const body: unknown = await response.json();
    if (body === null || typeof body !== "object") throw new Error("Missing usage response");
    data = body as UsageView;
  } catch {
    throw new Error("Usage response could not be read. Try again.");
  }
  if (data.run_id !== runId)
    throw new Error("Usage belongs to another run. Try again for the current run.");
  return data;
}
