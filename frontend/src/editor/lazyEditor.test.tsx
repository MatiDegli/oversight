import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

import { deferEditor } from "./lazyEditor";
import { loadedEditor, loadEditor } from "./loader";
import type { EditorImplementation } from "./loader";

vi.mock("./loader", () => ({ loadedEditor: vi.fn(), loadEditor: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
});

const select =
  () =>
  ({ text }: { text: string }) => <p>{text}</p>;

it("opens an idle-prefetched view synchronously without another Suspense fallback", () => {
  vi.mocked(loadedEditor).mockReturnValue({} as EditorImplementation);
  const View = deferEditor(select);
  expect(renderToStaticMarkup(<View text="Warm editor" />)).toBe("<p>Warm editor</p>");
  expect(loadEditor).not.toHaveBeenCalled();
});

it("keeps a local loading fallback for an open before prefetch has completed", () => {
  vi.mocked(loadedEditor).mockReturnValue(undefined);
  vi.mocked(loadEditor).mockReturnValue(new Promise(() => undefined));
  const View = deferEditor(select);
  expect(renderToStaticMarkup(<View text="Cold editor" />)).toContain("Loading editor…");
  expect(loadEditor).toHaveBeenCalledOnce();
});
