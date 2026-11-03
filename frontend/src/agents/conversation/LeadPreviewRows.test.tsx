import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import fixture from "../../../../fixtures/views/reference-cases.json";
import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../../state";
import { selectLeadConversation } from "./selectLeadConversation";
import LeadPreviewRows from "./LeadPreviewRows";

it("reuses held rows and renders the fallback once when no reply exists", () => {
  const state = (fixture.events as ServerEvent[]).reduce(applyEvent, createInitialState());
  const conversation = selectLeadConversation(state, "agent-9");
  // The reference fixture has no lead; use its real agent-independent status shape.
  const thread = conversation ?? { kind: "lead" as const, header: {} as never, entries: [] };
  const html = renderToStaticMarkup(
    <LeadPreviewRows
      conversation={{
        ...thread,
        entries: [
          {
            kind: "lead-view",
            seq: 20,
            event: {
              seq: 20,
              ts: "same",
              type: "lead.view",
              payload: {
                agent_id: "lead",
                through_seq: 1,
                caption: null,
                view: { kind: "preview", agent_id: "agent" },
              },
            },
          },
        ],
      }}
      renderView={() => <button>Preview app</button>}
    />,
  );
  expect(html.match(/Preview app/g)).toHaveLength(1);
  expect(html).toContain("Preview suggested; no matching lead reply is available.");
});
