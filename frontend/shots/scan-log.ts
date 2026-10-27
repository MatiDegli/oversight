// The log the Impeccable scan's page plays. The scan loads a URL and needs the page's navigation to
// finish, which it only does when the page holds no open connection: the live page keeps /events
// open for as long as it runs, so it never finishes (the scan failed with "Navigation timeout of
// 30000 ms exceeded"). So the scan loads the replay build instead, which makes no request after
// load and is the site judges open. A replay follows the log's own gaps, though, and the scan
// reads the page once, so every event takes the first one's time: the replay then has no pause and
// the page is complete at load. Seqs and payloads stay as they are.
//
//   node shots/scan-log.ts <event log> <settled log>
import { readFile, writeFile } from "node:fs/promises";
import { argv } from "node:process";

export function settleLog(jsonl: string): string {
  const events = jsonl
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as { ts?: unknown });
  const first = events[0]?.ts;
  if (typeof first !== "string")
    throw new Error("the event log is empty or its first event has no ts");
  return events.map((event) => JSON.stringify({ ...event, ts: first })).join("\n") + "\n";
}

if (import.meta.main) {
  const [from, to] = argv.slice(2);
  if (from === undefined || to === undefined) {
    throw new Error("usage: scan-log.ts <event log> <settled log>");
  }
  await writeFile(to, settleLog(await readFile(from, "utf8")));
}
