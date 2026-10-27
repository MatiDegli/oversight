/** Longest caller-to-callee depth on the module graph, with each cycle in one column. */
export function callColumns(ids: readonly string[], calls: readonly (readonly [string, string])[]) {
  const known = new Set(ids);
  const links = calls.filter(([a, b]) => a !== b && known.has(a) && known.has(b));
  const outgoing = new Map(
    ids.map((id) => [id, links.filter(([a]) => a === id).map(([, b]) => b)]),
  );
  const reachable = (start: string) => {
    const seen = new Set<string>();
    const pending = [start];
    while (pending.length) {
      const id = pending.pop();
      if (id === undefined || seen.has(id)) continue;
      seen.add(id);
      pending.push(...(outgoing.get(id) ?? []));
    }
    return seen;
  };
  const reach = new Map(ids.map((id) => [id, reachable(id)]));
  // The least code-unit id names a strongly connected component, independent of input order.
  const component = new Map(
    ids.map((id) => [
      id,
      [...ids].sort().find((other) => reach.get(id)?.has(other) && reach.get(other)?.has(id)) ?? id,
    ]),
  );
  const depth = new Map([...component.values()].map((id) => [id, 0]));
  for (let pass = 0; pass < depth.size; pass++) {
    let changed = false;
    for (const [a, b] of links) {
      const from = component.get(a) ?? a;
      const to = component.get(b) ?? b;
      if (from === to) continue;
      const next = (depth.get(from) ?? 0) + 1;
      if (next > (depth.get(to) ?? 0)) {
        depth.set(to, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return new Map(ids.map((id) => [id, depth.get(component.get(id) ?? id) ?? 0]));
}
