import type { ComponentProps } from "react";

import type { FileViewProps } from "./FileView";
import type { LiveDiffProps } from "./LiveDiff";
import { deferEditor } from "./lazyEditor";

export const EditorDiff = deferEditor<LiveDiffProps>(() =>
  import("./LiveDiff").then((module) => ({ default: module.EditorDiff })),
);
export const LiveDiff = deferEditor<LiveDiffProps>(() =>
  import("./LiveDiff").then((module) => ({ default: module.LiveDiff })),
);
export const FileView = deferEditor<FileViewProps>(
  () => import("./FileView").then((module) => ({ default: module.FileView })),
  true,
);
export const ReviewEditor = deferEditor<
  ComponentProps<typeof import("./ReviewEditor").ReviewEditor>
>(() => import("./ReviewEditor").then((module) => ({ default: module.ReviewEditor })));

export type { LiveDiffProps } from "./LiveDiff";
export { createEditorTheme } from "./theme";
export type { EditorColors } from "./theme";
export type { FileViewProps } from "./FileView";
export type { FileAccess, FileDraft } from "./fileDocument";

export { fileKey } from "./fileKey";
