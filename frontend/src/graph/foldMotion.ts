import type { AnimationOptions, Core, Position } from "cytoscape";

import { getActiveTheme } from "../theme";

import { drawEveryAnimationFrame } from "./animationFrames";
import { setScratch } from "./scratch";
import { settle } from "./updateGraph";

/** Slide only existing rows; compound frames follow their children, with no camera movement. */
export function slideRows(
  cy: Core,
  targets: ReadonlyMap<string, Position>,
  reduced: boolean,
): void {
  const moving = [...targets].filter(([id, p]) => {
    const node = cy.getElementById(id);
    return (
      node.nonempty() &&
      node.isChildless() &&
      (node.position("x") !== p.x || node.position("y") !== p.y)
    );
  });
  if (!moving.length) return;
  if (reduced || typeof requestAnimationFrame === "undefined") {
    cy.batch(() => {
      for (const [id, position] of moving) cy.getElementById(id).position(position);
    });
    settle(cy);
    return;
  }
  const theme = getActiveTheme();
  drawEveryAnimationFrame(cy);
  setScratch(cy, "foldAnimating", true);
  for (const [i, [id, position]] of moving.entries()) {
    cy.getElementById(id).animate(
      { position },
      {
        duration: Number.parseFloat(theme["transition-duration"]),
        easing: theme["transition-easing"] as AnimationOptions["easing"],
        queue: false,
        ...(i === moving.length - 1
          ? {
              step: () => {
                settle(cy);
              },
              complete: () => {
                setScratch(cy, "foldAnimating", false);
                settle(cy);
              },
            }
          : {}),
      },
    );
  }
}
