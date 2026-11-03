// Isolated acceptance only. No production import, card, preference or model suggestion mounts this.
import { useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import type { PreviewStatus, StartPreview } from "@contracts";
import { createPreviewAcceptancePort } from "../../transport";
import { createPreviewController } from "./previewController";
import { PreviewPanel } from "./PreviewPanel";
import { parsePreviewDetails, parsePreviewStatus } from "./previewTypes";

const params = new URLSearchParams(location.search);
const candidate = JSON.parse(params.get("candidate") ?? "null") as StartPreview;
document.documentElement.dataset.theme = params.get("theme") ?? "dark";
const controller = createPreviewController(
  createPreviewAcceptancePort(location.origin, {
    details: parsePreviewDetails,
    status: parsePreviewStatus,
  }),
  {
    schedule(callback, delay) {
      const timer = setTimeout(callback, delay);
      return () => {
        clearTimeout(timer);
      };
    },
  },
  { run: candidate.run_id, source: "isolated-scripted-fake" },
);
const api = {
  snapshot: controller.snapshot,
  see: () => controller.see(() => candidate),
  refresh: controller.refresh,
  close: controller.close,
  observe: (status: PreviewStatus) => {
    controller.observe(status);
  },
};
declare global {
  interface Window {
    previewAcceptance: typeof api;
  }
}
window.previewAcceptance = api;
window.addEventListener("pagehide", controller.dispose);

function AcceptanceFixture() {
  const view = useSyncExternalStore(controller.subscribe, controller.snapshot);
  return (
    <main className="preview-fixture">
      <h1 className="preview-fixture-caption">Isolated preview acceptance · scripted fake model</h1>
      <div className="preview-fixture-controls">
        <button
          type="button"
          onClick={() => {
            void api.see();
          }}
        >
          See preview
        </button>
      </div>
      <div className="preview-fixture-slot" data-visible={view.visible}>
        <PreviewPanel controller={controller} />
      </div>
    </main>
  );
}
const root = document.getElementById("root");
if (!root) throw new Error("Missing acceptance fixture root");
createRoot(root).render(<AcceptanceFixture />);
