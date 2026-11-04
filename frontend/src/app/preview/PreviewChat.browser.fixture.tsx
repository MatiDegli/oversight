import { StrictMode } from "react";
// Real App composition. Inputs may be fixtures; no controller, token or start function is exposed.
import { createRoot } from "react-dom/client";
import { App } from "../App";
import { PreviewPanel } from "./PreviewPanel";
import { fakeStatus } from "./previewFake";
import type { PreviewController, PreviewView } from "./previewPort";

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get("theme") ?? "dark";
const real = params.get("real") === "true";
document.addEventListener(
  "click",
  (event) => {
    if (
      event.target instanceof Element &&
      event.target.closest("button")?.textContent === "Preview app"
    ) {
      window.previewChatTiming = { clicked: performance.now() };
    }
  },
  true,
);
const root = document.getElementById("root");
if (!root) throw new Error("Missing Chat fixture root");
const fixture = params.get("panelFixture");
const view: PreviewView = {
  visible: true,
  owned: fixture === "uncertain",
  pending: false,
  uncertain: fixture === "uncertain",
  frameUrl: null,
  notice: fixture === "uncertain" ? "closure_unconfirmed" : null,
  status: fixture === "expired" ? fakeStatus("stopped", "lease_expired") : fakeStatus("ready"),
};
const inert = {
  snapshot: () => view,
  subscribe: () => () => {},
  close: () => Promise.resolve(),
} as unknown as PreviewController;
createRoot(root).render(
  <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
    <style>{`.preview-chat-watermark { display: none; } @media (max-width: 767px) { body:has(.chat-preview-slot) .preview-chat-watermark { display: block; position: fixed; bottom: 0; left: 0; right: 0; z-index: 60; padding: 2px 8px; background: var(--panel); font-size: 10px; } }`}</style>
    <p
      className="preview-chat-provenance"
      style={{ padding: "4px 12px", fontSize: "12px", color: "var(--muted)", flex: "none" }}
    >
      {real
        ? "Real local C shop · scripted fake coding model · fixture-authored lead inputs · Linux Chromium"
        : "Fixture · scripted inputs and shop · Linux Chromium"}
      {fixture && " · panel-state fixture"}
      <span className="preview-chat-watermark">
        {real
          ? "Real local C · fake coding model · fixture lead · Linux Chromium"
          : "Fixture · scripted inputs and shop · Linux Chromium"}
      </span>
    </p>
    <div style={{ flex: 1, minHeight: 0 }}>
      {fixture ? (
        <PreviewPanel controller={inert} replay={fixture === "replay"} />
      ) : (
        <StrictMode>
          <App />
        </StrictMode>
      )}
    </div>
  </div>,
);
