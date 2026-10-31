import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-measured-cone.jsonl?raw";
import additions from "../../../docs/additions.md?raw";
import { selectConeWords } from "../review";
import { applyEvent, createInitialState } from "../state";
import { themes } from "../theme";
import { elementSnapshot } from "./elementSnapshot";
import { graphElements } from "./elements";
import { foldElements } from "./foldElements";
import { HEADER, headerLayers, measuredLayer } from "./layers";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const stateAt = (seq: number) =>
  events.filter((e) => e.seq <= seq).reduce(applyEvent, createInitialState());
function projection(seq: number) {
  const state = stateAt(seq);
  const cone = state.changes["change-1"]?.proposal?.impact;
  if (!cone) throw new Error("Missing cone");
  const lit = new Set([...cone.seeds, ...cone.entries.map((e) => e.node_id)]);
  const measured = selectConeWords(state, "change-1");
  return {
    state,
    cone,
    lit,
    measured,
    elements: graphElements(state, false, lit, cone, false, measured),
  };
}

it.each([21, 22])("projects current measured caller words at seq %s", (seq) => {
  const { elements, cone } = projection(seq);
  const callers = elements.filter((e) => e.data.distance !== undefined);
  expect(callers).toHaveLength(9);
  const differs = callers.filter((e) => e.classes.includes("measured-differs"));
  expect(differs.map((e) => e.data.label)).toEqual(["quote_add"]);
  expect(differs[0]?.data.measured).toBe("differs");
  expect(callers.filter((e) => e.classes.includes("measured-same"))).toHaveLength(
    seq === 21 ? 8 : 0,
  );
  expect(callers.filter((e) => e.classes.includes("measured-unmeasured"))).toHaveLength(
    seq === 22 ? 7 : 0,
  );
  expect(
    callers.filter((e) => e.classes.includes("measured-varied")).map((e) => e.data.label),
  ).toEqual(seq === 22 ? ["subtotals"] : []);
  expect(
    elements.filter((e) => cone.seeds.includes(e.data.id)).every((e) => !e.data.measured),
  ).toBe(true);
});

it.each([23, 24])("keeps the previous canvas when nothing was compared at seq %s", (seq) => {
  const { state, cone, lit, measured, elements } = projection(seq);
  expect(measured).toBeNull();
  expect(elements.some((e) => e.classes.includes("measured-"))).toBe(false);
  expect(elements).toEqual(graphElements(state, false, lit, cone));
});

it("keeps null output identical and excludes live diff, watch and change nodes", () => {
  const { state, cone, lit, measured } = projection(21);
  expect(graphElements(state, false, lit, cone, false, null)).toEqual(
    graphElements(state, false, lit, cone),
  );
  const snapshot = elementSnapshot();
  for (const props of [{ liveCone: cone, litIds: [...lit] }, {}]) {
    expect(snapshot(state, { ...props, measured }).some((e) => e.data.measured)).toBe(false);
  }
  const allWords = new Map(state.graph?.nodes.map((n) => [n.id, "differs" as const]));
  const elements = graphElements(state, false, lit, cone, false, allWords);
  expect(
    elements.filter((e) => e.classes.split(" ").includes("pending")).every((e) => !e.data.measured),
  ).toBe(true);
  expect(snapshot(state, { cone, litIds: [...lit], measured }).some((e) => e.data.measured)).toBe(
    true,
  );
  expect(
    snapshot(state, { cone, litIds: [...lit], measured: null }).some((e) => e.data.measured),
  ).toBe(false);
});

it("folds differs alone into classes and modules without changing positions or routes", () => {
  const { elements, state, cone, lit } = projection(21);
  const expanded = new Set(state.graph?.nodes.filter((n) => n.kind === "module").map((n) => n.id));
  for (const modules of [new Set<string>(), new Set(expanded)]) {
    const before = foldElements(graphElements(state, false, lit, cone), expanded, modules);
    const after = foldElements(elements, expanded, modules);
    expect(after.visible).toEqual(before.visible);
    expect(
      after.elements.map((e) => [e.data.id, e.position, e.data.source, e.data.target]),
    ).toEqual(before.elements.map((e) => [e.data.id, e.position, e.data.source, e.data.target]));
    expect(
      after.elements.filter((e) => e.classes.includes("measured-differs")).map((e) => e.data.title),
    ).toEqual([modules.size ? "cart.py" : "Cart"]);
  }
  const varied = elements.map((e) =>
    e.data.measured
      ? {
          ...e,
          data: { ...e.data, measured: "varied" as const },
          classes: e.classes.replace(/measured-\w+/, "measured-varied"),
        }
      : e,
  );
  expect(
    foldElements(varied, expanded)
      .elements.filter((e) => e.classes.includes("folded"))
      .some((e) => e.data.measured),
  ).toBe(false);
});

it.each(["dark", "day"] as const)(
  "draws neutral words with the existing SVG fonts in %s",
  (theme) => {
    for (const word of ["differs", "varied"] as const) {
      const layer = measuredLayer(themes[theme], word, { width: 280, height: 46 });
      const svg = decodeURIComponent(layer.image.split(",").slice(1).join(","));
      expect(svg).toContain(
        `font-weight="${word === "differs" ? "600" : "400"}" fill="${themes[theme][word === "differs" ? "text" : "muted"]}">${word}</text>`,
      );
      expect(svg).toContain('text-anchor="end"');
      expect(svg).toContain("@font-face");
    }
    const folded = headerLayers(
      themes[theme],
      "Cart",
      "8 methods",
      { width: 280, height: 46 },
      "empty",
      undefined,
      true,
      "differs",
    );
    const svg = folded.map((layer) => decodeURIComponent(layer.image)).join("");
    expect(svg).toContain(">8 methods</text>");
    expect(svg).toContain(">differs</text>");
  },
);

it.each(["class", "module"])("keeps agent marks over measured words on a folded %s", (kind) => {
  const { elements, state } = projection(21);
  const pending = elements.map((e) =>
    e.data.id === "shop.cart:Cart.total"
      ? {
          ...e,
          classes: `${e.classes} pending pending-A marked`,
          data: {
            ...e.data,
            marks: {
              word: "changed" as const,
              slot: "A" as const,
              badges: [{ slot: "A" as const, filled: true }],
            },
          },
        }
      : e,
  );
  const expanded = new Set(state.graph?.nodes.filter((n) => n.kind === "module").map((n) => n.id));
  const folded = foldElements(
    pending,
    expanded,
    kind === "module" ? new Set(["shop.cart"]) : new Set(),
  );
  const row = folded.elements.find(
    (e) => e.data.id === (kind === "class" ? "shop.cart:Cart" : "shop.cart"),
  );
  if (!row?.data.marks) throw new Error("Missing folded agent marks");
  const size = { width: row.data.w ?? 280, height: row.data.h ?? 46 };
  const draw = (measured?: "differs") =>
    headerLayers(
      themes.dark,
      row.data.title ?? "",
      row.data.count ?? "",
      size,
      "empty",
      row.data.marks,
      true,
      measured,
    );
  const layers = draw("differs");
  expect(row.data.measured).toBeUndefined();
  expect(row.classes.split(" ")).not.toContain("measured-differs");
  expect(layers).toHaveLength(2);
  expect(layers).toEqual(draw());
  const svg = layers.map((layer) => decodeURIComponent(layer.image)).join("");
  expect(svg).toContain(">changed</text>");
  expect(svg).toContain(">A</text>");
  expect(svg).not.toContain(">differs</text>");
  expect(svg).not.toContain(`>${row.data.count ?? ""}</text>`);
});

it.each(["dark", "day"] as const)(
  "keeps folded header titles capped and count styling shared in %s",
  (mode) => {
    const theme = themes[mode];
    const size = { width: 960, height: 46 };
    const title = "A folded container with a title longer than the reserved title slot";
    const plain = headerLayers(theme, title, "12 methods", size, "empty", undefined, true);
    const measured = headerLayers(
      theme,
      title,
      "12 methods",
      size,
      "empty",
      undefined,
      true,
      "differs",
    );
    expect(measured[0]?.width).toBe(HEADER.titleWidth);
    expect(measured[0]).toEqual(plain[0]);
    expect(measured[1]).toEqual({ ...plain[1], offsetX: measured[1]?.offsetX });
    expect(measured[1]?.offsetX).toBeLessThan(plain[1]?.offsetX ?? 0);
    expect(decodeURIComponent(measured[1]?.image ?? "")).toContain(">12 methods</text>");
  },
);

it("credits the review rework for the caller list independently of canvas words", () => {
  const row = additions.split("\n").find((line) => line.startsWith("| 4 |"));
  expect(row).toContain("measured caller list `cf1e536`");
  expect(row).toContain("canvas words built;");
  expect(row).not.toContain("b431391");
});
