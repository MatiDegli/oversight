import { ChevronDown, ChevronRight, File, FileCode2, Folder, FolderOpen } from "lucide-react";
import { useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useFilesNavigation } from "../FilesContext";
import { fileMarks, visibleTree } from "../filesTree";
import type { FileMark, TreeRow } from "../filesTree";
import { isTyping } from "./useShell";
import { focusRing } from "./shared";
import { useTreeListing } from "./useTreeListing";
import type { FilesNavigation } from "./useTreeListing";

export function FilesTree() {
  const navigation = useFilesNavigation();
  return navigation ? (
    <ConnectedTree navigation={navigation} />
  ) : (
    <p className="p-3.5 text-sm text-muted">Files are available in the project view.</p>
  );
}

function ConnectedTree({ navigation }: { navigation: FilesNavigation }) {
  const { recorded, failure, tree, retry } = useTreeListing(navigation);
  const rows = useMemo(
    () => (tree ? visibleTree(tree, navigation.expanded) : []),
    [tree, navigation.expanded],
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="px-3.5 py-2 text-xs text-muted">
        {recorded ? "Recorded files" : "main"}
        {tree
          ? ` · ${String(tree.files.length)} files`
          : failure
            ? " · Files unavailable"
            : " · Loading files…"}
      </p>
      {failure && (
        <div role="alert" className="px-3.5 py-2 text-sm text-muted">
          Files could not refresh.{" "}
          <button type="button" className={`underline ${focusRing}`} onClick={retry}>
            Retry
          </button>
        </div>
      )}
      <p className="flex items-center gap-2 px-3.5 py-1.5 text-sm text-muted">
        <FolderOpen aria-hidden="true" className="size-3.5" />
        {navigation.state.run?.workspace ?? "Repository"}
      </p>
      <TreeViewport rows={rows} navigation={navigation} />
      {tree?.truncated && (
        <p role="status" className="px-3.5 py-2 text-xs text-muted">
          2,000 entries shown. More files are omitted.
        </p>
      )}
      {tree && rows.length === 0 && (
        <p className="p-3.5 text-sm text-muted">
          {recorded ? "No file paths in this recording yet." : "This workspace is empty."}
        </p>
      )}
    </div>
  );
}

function treeKey(
  event: KeyboardEvent,
  rows: TreeRow[],
  selected: string | undefined,
  navigation: FilesNavigation,
  activate: (path: string) => void,
) {
  const index = rows.findIndex((row) => row.path === selected);
  const row = rows[index];
  if (!row || event.ctrlKey || event.metaKey || event.altKey || event.nativeEvent.isComposing)
    return;
  const handled = [
    "ArrowDown",
    "ArrowUp",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "Enter",
    " ",
  ].includes(event.key);
  if (!handled) return;
  event.preventDefault();
  const { expanded, toggle, openFile } = navigation;
  if (event.key === "ArrowDown")
    activate(rows[Math.min(rows.length - 1, index + 1)]?.path ?? row.path);
  if (event.key === "ArrowUp") activate(rows[Math.max(0, index - 1)]?.path ?? row.path);
  if (event.key === "Home") activate(rows[0]?.path ?? row.path);
  if (event.key === "End") activate(rows.at(-1)?.path ?? row.path);
  if (event.key === "ArrowRight" && row.directory) {
    if (!expanded.has(row.path)) toggle(row.path);
    else if (rows[index + 1]?.parent === row.path) activate(rows[index + 1]?.path ?? row.path);
  }
  if (event.key === "ArrowLeft") {
    if (row.directory && expanded.has(row.path)) toggle(row.path);
    else if (row.parent) activate(row.parent);
  }
  if (event.key === "Enter" || event.key === " ") {
    if (row.directory) toggle(row.path);
    else openFile(row.path);
  }
}

function TreeViewport({ rows, navigation }: { rows: TreeRow[]; navigation: FilesNavigation }) {
  const [active, setActive] = useState<string | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const id = useId();
  const marks = fileMarks(navigation.state);
  const selected = rows.find((row) => row.path === active)?.path ?? rows[0]?.path;
  const activate = (path: string) => {
    setActive(path);
    // Only a direct key scrolls: a file refresh never moves focus or scroll position.
    requestAnimationFrame(() =>
      document.getElementById(`${id}-${path}`)?.scrollIntoView({ block: "nearest" }),
    );
  };
  return (
    <div
      ref={host}
      role="tree"
      aria-label="Repository files"
      aria-activedescendant={selected ? `${id}-${selected}` : undefined}
      tabIndex={0}
      className={`files-tree min-h-0 flex-1 overflow-auto px-1.5 pb-3 ${focusRing}`}
      onKeyDown={(event) => {
        treeKey(event, rows, selected, navigation, activate);
      }}
    >
      {rows.map((row) => (
        <TreeEntry
          key={row.path}
          row={row}
          marks={marks.get(row.path) ?? []}
          id={`${id}-${row.path}`}
          selected={row.path === selected}
          open={navigation.expanded.has(row.path)}
          onClick={() => {
            setActive(row.path);
            if (!isTyping(document.activeElement)) host.current?.focus({ preventScroll: true });
            if (row.directory) navigation.toggle(row.path);
            else navigation.openFile(row.path);
          }}
        />
      ))}
    </div>
  );
}

function TreeEntry({
  row,
  marks,
  id,
  selected,
  open,
  onClick,
}: {
  row: TreeRow;
  marks: FileMark[];
  id: string;
  selected: boolean;
  open: boolean;
  onClick: () => void;
}) {
  const Icon = row.directory
    ? open
      ? FolderOpen
      : Folder
    : row.path.endsWith(".py")
      ? FileCode2
      : File;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div
      id={id}
      role="treeitem"
      aria-label={row.deleted ? `${row.name} · Deleted` : row.name}
      aria-level={row.depth}
      aria-posinset={row.position}
      aria-setsize={row.siblings}
      aria-expanded={row.directory ? open : undefined}
      aria-selected={selected}
      title={row.path.endsWith(".py") ? `${row.path} · Python file` : row.path}
      className="files-tree-row"
      style={{ paddingLeft: `${String(8 + (row.depth - 1) * 16)}px` }}
      onMouseDown={(event) => {
        if (isTyping(document.activeElement)) event.preventDefault();
      }}
      onClick={onClick}
    >
      {row.directory ? (
        <Chevron aria-hidden="true" className="size-3 shrink-0" />
      ) : (
        <span className="w-3 shrink-0" />
      )}
      <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted" />
      <span className="truncate">{row.name}</span>
      <span className="ml-auto flex gap-1">
        {row.deleted && <span className="text-xs text-muted">Deleted</span>}
        {marks.map((mark) => (
          <span
            key={mark.slot}
            className="file-agent-mark"
            data-slot={mark.slot}
            data-mode={mark.mode}
            aria-label={`Agent ${mark.slot} · ${mark.mode === "pending" ? "Pending change" : mark.mode === "edit" ? "Editing" : "Reading"}`}
            title={`Agent ${mark.slot} · ${mark.mode}`}
          >
            {mark.slot}
          </span>
        ))}
      </span>
    </div>
  );
}
