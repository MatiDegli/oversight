import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
// eslint-disable-next-line no-restricted-imports -- Shared test fixture, never a product dependency.
import { readMoment, required } from "../state/overlap.fixture";
import { AgentCard } from "./AgentCard";
import { selectAgentCard } from "./selectors";

it.each([
  [1, "x", "x"],
  [2, "x and y", "x and y"],
  [4, "x, y and 2 more", "x, y, z and w"],
] as const)("uses the specified words and full title for %s names", (count, short, full) => {
  const data = required(selectAgentCard(readMoment(), "agent-2"));
  const nodes = ["x", "y", "z", "w"].slice(0, count).map((name) => ({ id: name, name }));
  const entry = { agentId: "agent-1", slot: "A" as const, nodes };
  data.overlap = { sharedEdits: [entry], readOfOthers: [entry] };
  for (const state of ["working", "ready_for_review"] as const) {
    const html = renderToStaticMarkup(
      <AgentCard data={{ ...data, agent: { ...data.agent, state } }} />,
    );
    expect(html).toContain(`title="Also changed by Agent A: ${full}"`);
    expect(html).toContain(`>Also changed by Agent A: ${short}</p>`);
    expect(html).toContain(`title="Read ${full}, which Agent A is changing"`);
    expect(html).toContain(`>Read ${short}, which Agent A is changing</p>`);
    const lines = required(
      html.match(
        /<p[^>]*title="(?:Also changed by Agent [^"]+|Read [^"]+, which Agent [^"]+)"[^>]*>/g,
      ),
    );
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line).toContain('class="shrink-0 truncate text-sm text-text"');
      expect(line).not.toMatch(/role=|tabindex=|text-agent/);
    }
    expect(html.indexOf("Also changed by")).toBeLessThan(html.indexOf(`title="Read ${full}`));
  }
});
it("renders no overlap lines without overlap", () => {
  const data = required(selectAgentCard(readMoment(), "agent-1"));
  const html = renderToStaticMarkup(<AgentCard data={data} />);
  expect(html).not.toContain("Also changed by");
  expect(html).not.toContain("which Agent");
});
