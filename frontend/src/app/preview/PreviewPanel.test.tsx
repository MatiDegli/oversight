import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PreviewPanel } from "./PreviewPanel";
import type { PreviewController, PreviewView } from "./previewPort";
import { fakeStatus } from "./previewFake";
import { previewReasonCopy } from "./previewReasons";
import {
  parsePreviewDetails,
  parsePreviewStatus,
  previewReasons,
  previewStates,
} from "./previewTypes";
import type { PreviewDetails, PreviewStatus } from "./previewTypes";

function render(patch: Partial<PreviewView> = {}) {
  const view: PreviewView = {
    visible: true,
    status: fakeStatus(),
    owned: true,
    pending: false,
    uncertain: false,
    frameUrl: null,
    notice: null,
    ...patch,
  };
  const controller: PreviewController = {
    snapshot: () => view,
    subscribe: () => () => undefined,
    see: vi.fn(),
    observe: vi.fn(),
    refresh: vi.fn(),
    close: vi.fn(),
    navigate: vi.fn(),
    setIdentity: vi.fn(),
    dispose: vi.fn(),
  };
  return renderToStaticMarkup(<PreviewPanel controller={controller} />);
}

it("preserves exact required keys and nulls with no private fields", () => {
  const status: PreviewStatus = fakeStatus("ready");
  const details: PreviewDetails = { status, url: null };
  expect(Object.keys(parsePreviewStatus(status)).sort()).toEqual([
    "agent_id",
    "change_id",
    "head_commit",
    "preview_id",
    "reason",
    "revision",
    "run_id",
    "state",
  ]);
  expect(Object.keys(parsePreviewDetails(details)).sort()).toEqual(["status", "url"]);
  expect(JSON.parse(JSON.stringify(details))).toEqual(details);
  expect(details.status.reason).toBeNull();
  expect(details.url).toBeNull();
});
it.each(previewStates)("requires appropriate reason for %s", (state) => {
  const status = { ...fakeStatus(), state };
  const active = state === "starting" || state === "ready";
  for (const reason of [null, ...previewReasons]) {
    const value = { ...status, reason };
    if (active === (reason === null)) expect(parsePreviewStatus(value)).toEqual(value);
    else expect(() => parsePreviewStatus(value)).toThrow();
  }
});
it.each([
  { preview_id: "preview-1" },
  { preview_id: `preview-${"A".repeat(32)}` },
  { revision: 0 },
  { revision: -1 },
  { revision: 1.5 },
  { revision: Infinity },
  { reason: undefined },
  { head_commit: " " },
  { run_id: "" },
  { state: "unknown" },
  { lease_token: "private" },
  { profile_version: "v1" },
  { owned: true },
])("rejects malformed or private status %j", (patch) => {
  expect(() => parsePreviewStatus({ ...fakeStatus(), ...patch })).toThrow();
});
it("rejects every missing required key, extra details keys and URL outside ready", () => {
  for (const key of Object.keys(fakeStatus())) {
    const value = Object.fromEntries(Object.entries(fakeStatus()).filter(([name]) => name !== key));
    expect(() => parsePreviewStatus(value)).toThrow();
  }
  for (const value of [
    { status: fakeStatus() },
    { url: null },
    { status: fakeStatus(), url: null, token: "private" },
    { status: fakeStatus(), url: "https://frame.invalid" },
    { status: fakeStatus("ready"), url: "" },
  ])
    expect(() => parsePreviewDetails(value)).toThrow();
});

it.each(previewStates)("renders %s with persistent accessible Close", (state) => {
  const html = render({
    status: fakeStatus(state, state === "starting" || state === "ready" ? null : "closed"),
  });
  expect(html).toContain('aria-label="Live preview panel"');
  expect(html).toContain(`>${state.charAt(0).toUpperCase()}${state.slice(1)}<`);
  expect(html).toContain("</svg>Close</button>");
  expect(html).not.toContain("iframe");
});
it.each(previewReasons)("maps %s exhaustively to fixed useful copy", (reason) => {
  const html = render({ status: fakeStatus("failed", reason) });
  expect(html).toContain(previewReasonCopy[reason]);
});
it("uses exact isolation and observer copy, never gives observers an iframe", () => {
  expect(render({ status: fakeStatus("failed", "isolation_unavailable") })).toContain(
    "Live preview unavailable: isolation C is unavailable for this session.",
  );
  const html = render({
    status: fakeStatus("ready"),
    owned: false,
    frameUrl: "https://frame.invalid",
  });
  expect(html).toContain("Preview in use in another tab");
  expect(html).not.toContain("iframe");
  expect(html.match(/<button/g)).toHaveLength(1);
});
it("only mounts owned ready details and retains an existing frame during uncertain closure", () => {
  const url = "https://frame.invalid/app";
  for (const state of ["ready", "stopping", "failed"] as const) {
    const html = render({
      status: fakeStatus(state, state === "ready" ? null : "cleanup_unverified"),
      frameUrl: url,
      uncertain: state !== "ready",
    });
    expect(html).toContain('sandbox="allow-forms"');
    expect(html).toContain('referrerPolicy="no-referrer"');
    expect(html).toContain('title="Live preview for agent-a"');
    expect(html).not.toMatch(/allow-scripts|allow-same-origin|lease_token/);
    if (state !== "ready") expect(html).toContain("current frame stays visible");
  }
});
it("renders no ordinary-failure frame and hides only after dismissal", () => {
  expect(render({ status: fakeStatus("failed", "launch_failed") })).not.toContain("iframe");
  expect(render({ visible: false })).toBe("");
});
it("does not announce successful closure while Stop is pending", () => {
  const html = render({ status: fakeStatus("stopping", "closed"), uncertain: true });
  expect(html).toContain("Waiting for verified closure.");
  expect(html).not.toContain("The preview was closed.");
});
