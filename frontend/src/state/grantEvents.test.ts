import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import { parseEvent } from "../transport";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

it("parses grant event lines and consumes them without changing product state", () => {
  const initial = createInitialState();
  const state = { ...initial, run: { run_id: "run-20261029-140000" } } as typeof initial;
  const ts = "2026-10-29T14:00:00.000Z";
  const events: ServerEvent[] = [
    {
      seq: 1,
      ts,
      type: "grant.created",
      payload: {
        grant_id: "grant-1",
        reason: "possible_callers",
        scope: { kind: "module", value: "shop.pricing", expires: "run" },
        change_id: "change-1",
        revision: 1,
        head_commit: "abc",
        run_id: "run-20261029-140000",
        session_id: null,
        granted_by: "person",
      },
    },
    { seq: 2, ts, type: "grant.revoked", payload: { grant_id: "grant-1" } },
  ];
  let next = state;
  for (const event of events) {
    expect(() => {
      next = applyEvent(next, parseEvent(JSON.stringify(event)));
    }).not.toThrow();
    expect(next).toEqual({ ...state, lastSeq: event.seq, clock: ts });
  }
});
