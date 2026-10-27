import type { editor, IRange } from "monaco-editor";
import { vi } from "vitest";

// Models normalize EOLs and hide the BOM unless explicitly requested. A recreated
// model repeats version 1, so identity/lifetime must also guard late GETs.
export function modelDouble(defaultEOL = "\n") {
  let text = "";
  let bom = "";
  let eol = defaultEOL;
  let version = 1;
  const listeners = new Set<(event: editor.IModelContentChangedEvent) => void>();
  const normalize = (value: string) => value.replace(/\r\n|\r|\n/g, eol);
  function emit(changes: editor.IModelContentChange[], isFlush = false, isEolChange = false) {
    version += 1;
    for (const listener of listeners)
      listener({
        changes,
        detailedReasonsChangeLengths: [],
        eol,
        versionId: version,
        isFlush,
        isEolChange,
        isUndoing: false,
        isRedoing: false,
      });
  }
  const offset = (range: IRange, end = false) => {
    const lines = text.split(eol);
    const line = end ? range.endLineNumber : range.startLineNumber;
    const column = end ? range.endColumn : range.startColumn;
    return (
      lines.slice(0, line - 1).reduce((sum, value) => sum + value.length + eol.length, 0) +
      column -
      1
    );
  };
  return {
    updateOptions: vi.fn(),
    dispose: vi.fn(),
    getValue: (_preference?: number, preserveBOM = false) => (preserveBOM ? bom : "") + text,
    getEOL: () => eol,
    getVersionId: () => version,
    setValue(value: string) {
      const endings = value.match(/\r\n|\r|\n/g) ?? [];
      eol =
        endings.length === 0
          ? defaultEOL
          : endings.filter((ending) => ending.includes("\r")).length > endings.length / 2
            ? "\r\n"
            : "\n";
      bom = value.startsWith("\uFEFF") ? "\uFEFF" : "";
      text = normalize(value.replace(/^\uFEFF/, ""));
      emit([], true);
    },
    setEOL(sequence: number) {
      const next = sequence === 0 ? "\n" : "\r\n";
      if (next === eol) return;
      const lines = text.split(eol);
      eol = next;
      text = lines.join(eol);
      emit([], false, true);
    },
    applyEdits(edits: editor.IIdentifiedSingleEditOperation[]) {
      const changes = edits
        .map(({ range, text: inserted }) => ({
          range,
          rangeOffset: offset(range),
          rangeLength: offset(range, true) - offset(range),
          text: normalize(inserted ?? ""),
        }))
        .sort((a, b) => b.rangeOffset - a.rangeOffset);
      for (const change of changes)
        text =
          text.slice(0, change.rangeOffset) +
          change.text +
          text.slice(change.rangeOffset + change.rangeLength);
      emit(changes);
      return [];
    },
    onDidChangeContent(listener: (event: editor.IModelContentChangedEvent) => void) {
      listeners.add(listener);
      return { dispose: () => listeners.delete(listener) };
    },
  } as unknown as editor.ITextModel;
}
