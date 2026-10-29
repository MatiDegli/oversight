import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

it("consumes grant events without changing product state", () => {
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
        run_id: state.run!.run_id,
        session_id: null,
        granted_by: "person",
      },
    },
    { seq: 2, ts, type: "grant.revoked", payload: { grant_id: "grant-1" } },
  ];
  let next = state;
  for (const event of events) {
    next = applyEvent(next, event);
    expect(next).toEqual({ ...state, lastSeq: event.seq, clock: ts });
  }
});
