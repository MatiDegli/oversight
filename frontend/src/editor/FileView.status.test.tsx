import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import { createFileSession } from "../app";
import { CommandError } from "../transport";
import { FileFeedback } from "./FileView";
import { FileDocument } from "./fileDocument";
import { modelDouble } from "./fileModel.testSupport";

vi.mock("./monaco", () => ({ monaco: { editor: { EndOfLineSequence: { LF: 0, CRLF: 1 } } } }));

function setup() {
  const getFile = vi.fn().mockResolvedValue({
    path: "shop/cart.py",
    content: "original",
    hash: "h0",
    agent_id: null,
  });
  const files = createFileSession({ getFile, saveFile: vi.fn() });
  const doc = new FileDocument(
    { path: "shop/cart.py", agentId: null, startLine: 11, endLine: 19 },
    modelDouble(),
    files,
    vi.fn(),
  );
  const render = () =>
    renderToStaticMarkup(
      <FileFeedback
        state={doc.state}
        fontError={false}
        onReload={() => {
          void doc.load(true);
        }}
      />,
    );
  return { doc, getFile, render };
}

it("reserves one caption line across the first load, clean state and a held refresh", async () => {
  const { doc, getFile, render } = setup();
  const initial = render();
  expect(initial).toContain("Loading file…");
  await doc.load();
  const clean = render();
  getFile.mockReturnValueOnce(new Promise(() => undefined));
  void doc.load();
  const refreshing = render();
  expect(refreshing).not.toContain("Loading file…");
  const row = /<p role="status"[^>]*>/;
  const status = clean.match(row)?.[0];
  expect(status).toContain("h-(--caption-line-height)");
  expect(status).toContain("leading-(--caption-line-height)");
  expect(status).not.toContain("p-4");
  expect(initial.match(row)?.[0]).toBe(status);
  expect(refreshing.match(row)?.[0]).toBe(status);
  const css = readFileSync(new URL("../theme/index.css", import.meta.url), "utf8");
  expect(css).toContain("--caption-line-height: 20px;");
  doc.dispose();
});

it.each([
  new CommandError(501, undefined, "Not implemented"),
  new CommandError(404, undefined, "Not found"),
  new TypeError("Failed to fetch"),
])(
  "a failed first load shows an alert and Reload without a permanent loading caption: %s",
  async (error) => {
    const { doc, getFile, render } = setup();
    getFile.mockRejectedValueOnce(error);
    await doc.load();
    expect(doc.state).toMatchObject({ phase: "failed", loaded: false });
    const failed = render();
    expect(failed).toContain('role="alert"');
    expect(failed).toContain(">Reload</button>");
    expect(failed).not.toContain("Loading file…");
    doc.dispose();
  },
);
