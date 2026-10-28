import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import type { GraphViewProps } from "../../graph";
import { Frame } from "../Frame";
import { tabbedView } from "../view";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";

const probes = vi.hoisted(() => ({ shared: vi.fn(), graph: vi.fn(), phone: false }));
vi.mock("../../graph", async (original) => ({
  ...(await original<typeof import("../../graph")>()),
  GraphView: (props: GraphViewProps) => {
    probes.graph(props);
    return <div aria-label="Repository graph" />;
  },
}));
vi.mock("../../editor/monaco", () => ({ monaco: { editor: {} } }));
vi.mock("../shell/GraphStage", async (original) => {
  const { GraphStage } = await original<typeof import("../shell/GraphStage")>();
  return {
    GraphStage: (props: Parameters<typeof GraphStage>[0]) => {
      probes.shared(props);
      return <GraphStage {...props} />;
    },
  };
});
vi.mock("./usePhonePractice", () => ({ usePhonePractice: () => probes.phone }));
afterEach(() => vi.clearAllMocks());
const practice = validateManifest(manifest, recording);
const focus = {
  ...tabbedView,
  id: "practice",
  review: "change-1",
  frameIds: ["shop.pricing:compute_price"],
  frameKey: "practice:change-1",
};
function render(available: boolean, active = false) {
  return renderToStaticMarkup(
    <Frame
      state={practice.cut}
      view={tabbedView}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
      practice={available ? { active, focus, content: "Practice", open: vi.fn() } : undefined}
    />,
  );
}
it.each([false, true])(
  "keeps practice graph composition owned by challenge (phone=%s)",
  (phone) => {
    probes.phone = phone;
    const html = render(true, phone);
    expect(probes.shared).not.toHaveBeenCalled();
    expect(probes.graph).toHaveBeenCalledOnce();
    expect(html.match(/aria-label="Repository graph"/g)).toHaveLength(1);
    if (phone) {
      expect(html).toContain("graph-cone");
      expect(probes.graph.mock.calls[0]?.[0]).toMatchObject({
        frameKey: "practice:change-1::phone",
      });
    }
  },
);
it("keeps ordinary replay on the shared graph composition", () => {
  probes.phone = false;
  render(false);
  expect(probes.shared).toHaveBeenCalledOnce();
  expect(probes.shared.mock.calls[0]?.[0]).not.toHaveProperty("stacked");
  expect(probes.graph).toHaveBeenCalledOnce();
});
