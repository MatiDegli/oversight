import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { ServerEvent } from "@contracts";

export interface Hold {
  seq: number;
  ts: string;
  after: string;
  delay: number;
}
type Holding = Window & { e2eHolds: { held: number[]; release(delay: number): void } };

export function before(events: ServerEvent[], event: ServerEvent, delay: number): Hold {
  const previous = events[event.seq - 2];
  if (!previous || previous.ts === event.ts) throw new Error("Hold must start a timestamp group");
  return { seq: event.seq, ts: event.ts, after: previous.ts, delay };
}

// As in level 1, hold only windows an event would close. Move its clock reading, never the
// exported log's bytes or identifiers; resume only after the assertions finish.
export function installHolds(holds: Hold[]) {
  const parse = Date.parse.bind(Date);
  const moved = new Map<string, number>();
  for (const { ts, after, delay } of holds)
    moved.set(ts, (moved.get(after) ?? parse(after)) + delay);
  Date.parse = (text: string) => moved.get(text) ?? parse(text);
  const delays = new Set(holds.map(({ delay }) => delay));
  const held: number[] = [];
  const waiting = new Map<number, () => void>();
  const start = window.setTimeout.bind(window);
  window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
    if (
      typeof handler === "function" &&
      delay !== undefined &&
      delays.has(delay) &&
      !waiting.has(delay)
    ) {
      waiting.set(delay, () => {
        start(handler, 0, ...args);
      });
      held.push(delay);
      return 0;
    }
    return start(handler, delay, ...args);
  }) as typeof window.setTimeout;
  Object.defineProperty(window, "e2eHolds", {
    value: {
      held,
      release(delay: number) {
        waiting.get(delay)?.();
      },
    },
  });
}

export async function engaged(page: Page, hold: Hold, timeout?: number) {
  await expect
    .poll(() => page.evaluate(() => (window as unknown as Holding).e2eHolds.held), {
      ...(timeout === undefined ? {} : { timeout }),
    })
    .toContain(hold.delay);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", String(hold.seq - 1));
}
export const release = (page: Page, hold: Hold) =>
  page.evaluate((delay) => {
    (window as unknown as Holding).e2eHolds.release(delay);
  }, hold.delay);
