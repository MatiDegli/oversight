/**
 * How long before `now` a step arrived, in the card's words: "now", "40 s ago", "2 min ago",
 * "1 h ago". Both are event timestamps, never the reader's clock, so a replay reads as it did live.
 * Empty when either is missing or unreadable: no age is better than a wrong one.
 */
export function ageWords(then: string | undefined, now: string | null): string {
  if (then === undefined || now === null) return "";
  const seconds = (Date.parse(now) - Date.parse(then)) / 1000;
  if (Number.isNaN(seconds)) return "";
  if (seconds < 5) return "now";
  if (seconds < 60) return `${String(Math.floor(seconds))} s ago`;
  if (seconds < 3600) return `${String(Math.floor(seconds / 60))} min ago`;
  return `${String(Math.floor(seconds / 3600))} h ago`;
}
