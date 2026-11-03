import type { State } from "../../state";
// eslint-disable-next-line no-restricted-imports -- Wrap the landed Chat projection without replacing its facts.
import { selectChatRows } from "../../agents/chatLeadSelectors";
import { presentationFor, selectPresentation } from "../../teams/presentation";

export type ChatRow = ReturnType<typeof selectChatRows>[number];
export type ChatMember = Extract<ChatRow, { kind: "agent" }>;

/** Membership and first-observed order belong to K3, including teams sharing paint. */
export function chatTeamGroups(state: State) {
  const rows = selectChatRows(state);
  const { teamOrder } = selectPresentation(state);
  return teamOrder.flatMap((id) => {
    const teamRows = rows.filter((row) => row.team === id);
    const first = teamRows[0];
    if (!first) return [];
    const identity = presentationFor(state, first.id);
    if (!identity) return [];
    const lead = teamRows.find((row) => row.kind === "lead");
    const members = teamRows.filter((row): row is ChatMember => row.kind === "agent");
    const working = members.filter((row) =>
      ["working", "revising"].includes(state.agents[row.id]?.state ?? ""),
    ).length;
    const needsYou = members.filter((row) => row.ready).length;
    const counts = [
      `${String(members.length)} ${members.length === 1 ? "agent" : "agents"}`,
      working ? `${String(working)} working` : null,
      needsYou ? `${String(needsYou)} ${needsYou === 1 ? "needs" : "need"} you` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const title = `${identity.kind === "lead" ? "Lead" : "Plan"} ${identity.letter}`;
    return [
      {
        id,
        identity,
        lead,
        members,
        grouped: identity.kind !== "agent",
        title,
        summary: `${title} · ${lead?.teamLine ?? counts}`,
      },
    ];
  });
}
export type ChatTeamGroup = ReturnType<typeof chatTeamGroups>[number];
