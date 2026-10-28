import type { FileContent, FileSaved, SaveFile } from "@contracts";
import type { editor } from "monaco-editor";

import { CommandError, commandFailure } from "../transport";
import { monaco } from "./monaco";
import { fileKey } from "./fileKey";

export { fileKey } from "./fileKey";

export interface FileLocation {
  path: string;
  agentId: string | null;
  startLine: number;
  endLine: number;
}

export interface FileDraft {
  content: string;
  baseHash: string;
}

/** Page-owned state. Saving and its reconciliation must survive the editor's disposal. */
export interface FileAccess {
  drafts: Map<string, FileDraft>;
  inFlightSaves: Map<string, Promise<FileSaved>>;
  saved: Map<string, FileContent>;
  readFile: (path: string, agent?: string) => Promise<FileContent>;
  saveFile: (body: SaveFile) => Promise<FileSaved>;
}

export const staleMessage =
  "This file changed on the server since you started editing. Your edits are still here.";

export interface FileState {
  phase: "loading" | "clean" | "dirty" | "saving" | "stale" | "failed";
  dirty: boolean;
  message: string | null;
  loaded: boolean;
  retryLoad: boolean;
  canSave: boolean;
}

/** One model's lifetime. Every async answer is guarded by lifetime and request generation. */
export class FileDocument {
  private active = true;
  private generation = 0;
  private loaded: { content: string; hash: string } | undefined;
  private fetched: FileContent | undefined;
  private stale = false;
  private failure: string | null = null;
  private reading = true;
  private failedRead = false;
  private commit: string | undefined;
  private following: Promise<FileSaved> | undefined;
  private readonly subscription: { dispose: () => void };
  private replacing = false;
  private readonly key: string;
  private readonly writable: boolean;
  private lineEndings: string[] = [];

  constructor(
    readonly target: FileLocation,
    readonly model: editor.ITextModel,
    private readonly files: FileAccess,
    private readonly changed: (state: FileState) => void,
    private readonly replaceModel: (text: string) => void = (text) => {
      model.setValue(text);
    },
    review = false,
  ) {
    this.key = fileKey(target.path, target.agentId);
    this.writable = target.agentId === null || review;
    const draft = this.writable ? files.drafts.get(this.key) : undefined;
    if (draft) this.replace(draft.content);
    this.subscription = model.onDidChangeContent((event) => {
      if (this.replacing) return;
      if (event.isFlush) {
        this.lineEndings = model.getValue().match(/\r\n|\r|\n/g) ?? [];
      } else if (!event.isEolChange) {
        // Monaco's ranges refer to the pre-edit document. Apply later ranges first so
        // inserting or deleting lines preserves the untouched source terminators.
        for (const change of [...event.changes].sort((a, b) => b.rangeOffset - a.rangeOffset)) {
          this.lineEndings.splice(
            change.range.startLineNumber - 1,
            change.range.endLineNumber - change.range.startLineNumber,
            ...(change.text.match(/\r\n|\r|\n/g) ?? []),
          );
        }
      }
      this.syncDraft();
      this.emit();
    });
    const pending = this.pending();
    if (pending) this.followSave(pending);
  }

  private pending() {
    return this.writable ? this.files.inFlightSaves.get(this.key) : undefined;
  }

  get state(): FileState {
    const dirty = this.loaded
      ? this.model.getValue() !== this.loaded.content
      : this.writable && this.files.drafts.has(this.key);
    return {
      dirty,
      loaded: this.loaded !== undefined,
      retryLoad: this.failedRead,
      canSave:
        this.writable && this.loaded !== undefined && dirty && !this.pending() && !this.stale,
      phase: this.pending()
        ? "saving"
        : this.failure
          ? "failed"
          : this.stale
            ? "stale"
            : this.reading
              ? "loading"
              : dirty
                ? "dirty"
                : "clean",
      message: this.failure ?? (this.stale ? staleMessage : null),
    };
  }

  private emit() {
    if (this.active) this.changed(this.state);
  }

  private syncDraft() {
    if (!this.writable || !this.loaded) return;
    if (this.state.dirty || this.pending()) {
      this.files.drafts.set(this.key, {
        content: this.content(),
        baseHash: this.loaded.hash,
      });
    } else this.files.drafts.delete(this.key);
  }

  private content() {
    let line = 0;
    return this.model
      .getValue(undefined, true)
      .replace(/\r\n|\r|\n/g, () => this.lineEndings[line++] ?? this.model.getEOL());
  }

  private replace(text: string) {
    this.replacing = true;
    try {
      if (this.model.getValue(undefined, true) !== text) this.replaceModel(text);
      const endings = text.match(/\r\n|\r|\n/g) ?? [];
      // Match Monaco's majority rule, but never inherit the browser OS for a one-line file.
      this.model.setEOL(
        endings.filter((ending) => ending.includes("\r")).length > endings.length / 2
          ? monaco.editor.EndOfLineSequence.CRLF
          : monaco.editor.EndOfLineSequence.LF,
      );
      this.lineEndings = endings;
    } finally {
      this.replacing = false;
    }
    return this.model.getValue();
  }

  /** For a saved response or a restored draft, without replacing the person's current edits. */
  private normalized(text: string) {
    return text.replace(/^\uFEFF/, "").replace(/\r\n|\r|\n/g, this.model.getEOL());
  }

  async load(reload = false): Promise<void> {
    const generation = ++this.generation;
    const model = this.model;
    const version = model.getVersionId();
    const clean = !this.state.dirty;
    this.reading = true;
    this.emit();
    try {
      const answer = await this.files.readFile(this.target.path, this.target.agentId ?? undefined);
      if (!this.active || generation !== this.generation || model !== this.model) return;
      const draft = this.writable ? this.files.drafts.get(this.key) : undefined;
      this.fetched = answer;
      const unchanged = model.getVersionId() === version;
      if (unchanged && (reload || clean) && (!draft || reload)) {
        this.loaded = { content: this.replace(answer.content), hash: answer.hash };
        this.stale = false;
      } else {
        this.loaded ??= {
          content: this.normalized(answer.content),
          hash: draft?.baseHash ?? answer.hash,
        };
        this.stale = answer.hash !== this.loaded.hash;
      }
      this.failure = null;
      this.failedRead = false;
      this.reading = false;
      this.syncDraft();
      this.emit();
    } catch (error) {
      if (!this.active || generation !== this.generation) return;
      this.failure = commandFailure("Loading the file", error);
      this.failedRead = true;
      this.reading = false;
      this.emit();
    }
  }

  observeCommit(commit: string) {
    const previous = this.commit;
    this.commit = commit;
    if (
      previous !== undefined &&
      previous !== commit &&
      this.loaded &&
      !this.state.dirty &&
      !this.pending()
    )
      void this.load();
  }

  save() {
    if (!this.writable || !this.loaded || !this.state.dirty || this.pending() || this.stale) return;
    this.syncDraft();
    ++this.generation;
    this.failure = null;
    this.failedRead = false;
    this.reading = false;
    const pending = this.files.saveFile({
      path: this.target.path,
      content: this.content(),
      base_hash: this.loaded.hash,
      ...(this.target.agentId === null ? {} : { agent_id: this.target.agentId }),
    });
    this.followSave(pending);
    this.emit();
  }

  private followSave(pending: Promise<FileSaved>) {
    if (this.following === pending) return;
    this.following = pending;
    void pending.then(
      () => {
        if (!this.active) return;
        ++this.generation;
        const saved = this.files.saved.get(this.key);
        if (saved) {
          this.loaded = { content: this.normalized(saved.content), hash: saved.hash };
          this.fetched = saved;
        }
        this.stale = false;
        this.failure = null;
        this.failedRead = false;
        this.reading = false;
        this.syncDraft();
        this.emit();
      },
      (error: unknown) => {
        if (!this.active) return;
        this.stale = error instanceof CommandError && error.code === "stale_file";
        this.failure = this.stale ? null : commandFailure("Saving the file", error);
        this.emit();
      },
    );
  }

  discard() {
    if (!this.fetched || this.pending()) return;
    ++this.generation;
    this.loaded = { content: this.replace(this.fetched.content), hash: this.fetched.hash };
    this.stale = false;
    this.failure = null;
    this.failedRead = false;
    this.reading = false;
    this.syncDraft();
    this.emit();
  }

  dispose() {
    this.syncDraft();
    this.active = false;
    ++this.generation;
    this.subscription.dispose();
  }
}
