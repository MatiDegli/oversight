import type { ComponentProps } from "react";

import type { FileViewProps } from "./FileView";
import type { LiveDiffProps } from "./LiveDiff";
import { deferEditor } from "./lazyEditor";

export const EditorDiff = deferEditor<LiveDiffProps>((module) => module.EditorDiff);
export const LiveDiff = deferEditor<LiveDiffProps>((module) => module.LiveDiff);
export const FileView = deferEditor<FileViewProps>((module) => module.FileView, true);
export const ReviewEditor = deferEditor<
  ComponentProps<typeof import("./ReviewEditor").ReviewEditor>
>((module) => module.ReviewEditor);

export { prefetchEditorWhenIdle } from "./prefetch";

export type { LiveDiffProps } from "./LiveDiff";
export { createEditorTheme } from "./theme";
export type { EditorColors } from "./theme";
export type { FileViewProps } from "./FileView";
export type { FileAccess, FileDraft } from "./fileDocument";

export { fileKey } from "./fileKey";

export { languageForPath } from "./language";
export { FileViewHeader } from "./FileViewHeader";
