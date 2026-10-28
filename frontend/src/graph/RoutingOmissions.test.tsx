import type { ImpactCone } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  count: 0,
  effects: [] as (() => (() => void) | undefined)[],
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: () => [
    hooks.count,
    (count: number) => {
      hooks.count = count;
    },
  ],
  useEffect: (effect: () => (() => void) | undefined) => hooks.effects.push(effect),
}));
vi.mock("../editor", () => ({ EditorDiff: () => null }));

import { ImpactConeCard } from "../review";
import { RoutingOmissions } from "./RoutingOmissions";

afterEach(() => {
  hooks.count = 0;
  hooks.effects.length = 0;
});

it("retains the last count as a prop across cone card unmount and remount, without another event", () => {
  const host = new EventTarget();
  const hostRef = { current: host as HTMLDivElement };
  let count = 0;
  RoutingOmissions({
    hostRef,
    inCone: true,
    onCount: (next) => {
      count = next;
    },
  });
  const dispose = hooks.effects[0]?.();
  const cone: ImpactCone = {
    seeds: [],
    entries: [],
    edges_followed: "exact",
    max_depth: 3,
    max_nodes: 20,
    tests_reaching: 0,
    omitted_by_depth: 0,
    omitted_by_limit: 0,
  };
  const card = () =>
    renderToStaticMarkup(<ImpactConeCard cone={cone} nodes={[]} routingOmissions={count} />);
  try {
    host.dispatchEvent(new CustomEvent("routingomissions", { detail: 3 }));
    expect(card()).toContain("+3 call routes omitted: no clear lane");
    // The shell retains count while no card exists; the watch notice uses the same last count.
    expect(renderToStaticMarkup(RoutingOmissions({ hostRef }))).toContain("+3 call routes");
    expect(card()).toContain('role="status"');
    expect(card()).toContain("+3 call routes omitted: no clear lane");
    expect(RoutingOmissions({ hostRef, inCone: true })).toBeNull();
  } finally {
    dispose?.();
  }
});
