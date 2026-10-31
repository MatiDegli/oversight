import cytoscape from "cytoscape";
import { expect, it } from "vitest";
import { exposeProbe } from "./probe";
import type { GraphProbeHost } from "./probe";

it("attaches, notifies and disposes safely on a plain-object host", () => {
  const cy = cytoscape({ headless: true });
  const host = {} as GraphProbeHost;
  try {
    const dispose = exposeProbe(host, cy);
    expect(host.oversightGraph).toBeDefined();
    expect(() => {
      cy.emit("render");
      cy.emit("resize");
      dispose();
      cy.emit("render");
      cy.emit("resize");
    }).not.toThrow();
    expect(host.oversightGraph).toBeUndefined();
  } finally {
    cy.destroy();
  }
});

it("returns only drawn body boxes in screen pixels and notifies until disposed", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [{ data: { id: "a" }, position: { x: 40, y: 50 } }, { data: { id: "b" } }],
  });
  const host = new EventTarget() as GraphProbeHost;
  let notifications = 0;
  host.addEventListener("graphrender", () => {
    notifications++;
  });
  const dispose = exposeProbe(host, cy);
  try {
    expect(notifications).toBe(1);
    cy.zoom(2);
    cy.pan({ x: 10, y: 20 });
    expect(host.oversightGraph?.renderedBox("a")).toEqual(
      cy.$id("a").renderedBoundingBox({
        includeLabels: false,
        includeOverlays: false,
        includeUnderlays: false,
      }),
    );
    expect(host.oversightGraph?.renderedBox("absent")).toBeNull();
    cy.scratch("foldAliases", new Map([["b", "a"]]));
    expect(host.oversightGraph?.renderedBox("b")).toBeNull();
    cy.$id("a").style("display", "none");
    expect(host.oversightGraph?.renderedBox("a")).toBeNull();
    const before = notifications;
    cy.emit("render");
    cy.emit("resize");
    expect(notifications).toBe(before + 2);
    dispose();
    cy.emit("render");
    cy.emit("resize");
    expect(notifications).toBe(before + 2);
  } finally {
    dispose();
    cy.destroy();
  }
});
