import { describe, expect, it } from "vitest";
import type { UsageView } from "@contracts";
import { duration, figure, groupings, money, rows, selection, showGrouping, totals } from "./model";

const view = (): UsageView => ({
  run_id: "run-1",
  seq: 9,
  revision_rounds: null,
  teams: [],
  teams_add_up: false,
  cost: {
    source_url: "https://example.com",
    retrieved: "2026-10-30",
    calls: 5,
    priced_calls: 3,
    unpriced_calls: 2,
    ranged_calls: 0,
    known_usd: 0.001,
    usd: null,
    accepted_changes: 0,
    usd_per_accepted: null,
    known_usd_per_accepted: null,
    coverage: "missing_metrics",
    problems: [],
    rows: [
      {
        team: "agent-1",
        agent_id: "agent-1",
        role: null,
        model: null,
        priced: true,
        calls: 3,
        known_usd: 0.001,
        usd: 0.001,
      },
      {
        team: "agent-1",
        agent_id: "agent-1",
        role: "review",
        model: "open",
        priced: false,
        calls: 2,
        known_usd: 0,
        usd: null,
      },
    ],
  },
  hours: {
    known_agent_ms: 33000,
    agent_ms: null,
    known_active_ms: 21000,
    active_ms: null,
    parallelism: null,
    coverage: "missing_metrics",
    problems: [],
    actors: [
      {
        team: "agent-1",
        agent_id: "agent-1",
        role: "agent",
        model: null,
        change_id: "change-9",
        ms: 30000,
        open: false,
      },
      {
        team: null,
        agent_id: null,
        role: "review",
        model: "open",
        change_id: "change-9",
        ms: 3000,
        open: false,
      },
    ],
  },
});
describe("usage display semantics", () => {
  it("formats subcent money without erasing significant digits or true zero", () => {
    expect([0, 0.00009089, 0.000977554, 0.009999, 0.01, 1.999].map(money)).toEqual([
      "US$0",
      "US$0.0000909",
      "US$0.000978",
      "US$0.0100",
      "US$0.01",
      "US$2.00",
    ]);
  });
  it("carries rounded duration boundaries", () => {
    expect([33000, 59949, 59999, 125000, 3599999, 3900000, 7199999].map(duration)).toEqual([
      "33.0 s",
      "59.9 s",
      "1 min 00 s",
      "2 min 05 s",
      "1 h 00 min",
      "1 h 05 min",
      "2 h 00 min",
    ]);
  });
  it("keeps exact, lower-bound and unknown zero separate", () => {
    expect(figure({ value: 0, known: 0 }, money)).toBe("US$0");
    expect(figure({ value: null, known: 0 }, money)).toBe("At least US$0");
    expect(figure({ value: null, known: null }, money)).toBe("—");
  });
  it("joins nullable identities and keeps review cost with its author", () => {
    const data = view();
    const [author, reviews] = rows(data, "agent");
    expect(author).toMatchObject({
      id: "agent-1",
      calls: 5,
      cost: { value: null, known: 0.001 },
      time: { value: 30000 },
    });
    expect(reviews).toMatchObject({
      id: "unknown",
      calls: null,
      cost: { known: null },
      time: { value: 3000 },
    });
    expect(rows(data, "model").map((row) => row.id)).toEqual(["unknown", "open"]);
    expect(rows(data, "role")[0]?.id).toBe("unknown");
  });
  it("preserves observed time alongside unavailable plan and lead work", () => {
    const data = view();
    data.cost.rows[0]!.role = "plan";
    expect(rows(data, "agent")[0]?.time).toEqual({ value: null, known: 30000 });
    expect(rows(data, "role").find((row) => row.id === "plan")?.time).toEqual({
      value: null,
      known: null,
    });
    data.hours.actors[0]!.ms = null;
    expect(rows(data, "agent")[0]?.time).toEqual({ value: null, known: null });
  });
  it("suppresses affected exact figures on incomplete coverage and uses response totals", () => {
    const data = view();
    data.cost.coverage = "incomplete";
    data.hours.coverage = "incomplete";
    expect(rows(data, "model")[0]?.cost.value).toBeNull();
    expect(rows(data, "agent")[0]?.time.value).toBeNull();
    expect(totals(data)).toMatchObject({
      cost: { value: null, known: 0.001 },
      time: { value: null, known: 33000 },
      perAccepted: { value: null, known: null },
    });
    expect(data.revision_rounds).toBeNull();
  });
  it("defaults to Agent, retains eligible selection and offers Team only for plans", () => {
    const data = view();
    expect(selection(data)).toBe("agent");
    expect(selection(data, "model")).toBe("model");
    expect(groupings(data)).toEqual(["agent", "model", "role"]);
    expect(showGrouping(data)).toBe(true);
    data.teams = [1, 2].map((id) => ({
      team: `plan-${String(id)}`,
      kind: "plan",
      agents: [],
      priced: true,
      calls: 0,
      known_usd: 0,
      usd: 0,
      known_ms: 0,
      ms: 0,
    }));
    expect(selection(data)).toBe("team");
    expect(selection(data, "agent")).toBe("agent");
    data.teams.pop();
    expect(selection(data)).toBe("agent");
  });
});
