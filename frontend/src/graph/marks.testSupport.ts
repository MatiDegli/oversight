import type { PaletteKey } from "../theme";
import { agentLook } from "../theme";
import type { AgentMark } from "./agentMarks";
import type { NodeMarks, Word } from "./layers";

/** Explicit renderer input; no runtime slot is used to recover identity from paint. */
export function mark(
  hue: PaletteKey = "A",
  word: Word = "changed",
  label = hue as string,
  agentId = `fixture-${label}`,
): AgentMark {
  return {
    agentId,
    team: `team-${label.split("·")[0] ?? label}`,
    kind: "agent",
    letter: label.split("·")[0] ?? label,
    label,
    planId: null,
    subtask: label.includes("·") ? Number(label.split("·")[1]) : null,
    hue,
    family: agentLook[hue].family,
    order: label.charCodeAt(0),
    word,
    work: word === "editing" ? "edit" : word === "read" ? "read" : undefined,
    ready: word === "changed" || word === "new",
    pending: word === "changed" || word === "new",
    filled: word !== "read",
  };
}
export function marks(
  hue: PaletteKey = "A",
  word: Word = "changed",
  label = hue as string,
): NodeMarks {
  const owner = mark(hue, word, label);
  return { word, owner, badges: [owner] };
}
