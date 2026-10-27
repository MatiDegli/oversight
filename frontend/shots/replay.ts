import type { ServerResponse } from "node:http";

import { replayEventsAfter } from "./plan.ts";

/** Keep the derived cut open like the real player; EOF would paint a transport error in shots. */
export function streamReplay(
  response: Pick<ServerResponse, "writeHead" | "write" | "on">,
  jsonl: string,
  after: number,
): void {
  response.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Access-Control-Allow-Origin": "*",
  });
  response.write(replayEventsAfter(jsonl, after));
  const heartbeat = setInterval(() => response.write(": heartbeat\n\n"), 1000);
  heartbeat.unref();
  response.on("close", () => {
    clearInterval(heartbeat);
  });
}
