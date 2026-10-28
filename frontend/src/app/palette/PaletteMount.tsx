import { useState } from "react";
import { createCommandClient, replayBuild } from "../../transport";
import { useFilesNavigation } from "../FilesContext";
import type { FrameProps } from "../Frame";
import { useTreeListing } from "../shell/useTreeListing";
import type { useShell } from "../shell/useShell";
import { panelOpen } from "../view";
import { paletteCommands } from "./commands";
import { Palette } from "./Palette";
import { paletteItems } from "./search";
import type { PaletteCommand } from "./search";

type Shell = ReturnType<typeof useShell>;
interface MountProps {
  frame: FrameProps;
  shell: Shell;
  onNode: (id: string) => void;
  onFocusTask?: () => void;
}

export function PaletteMount({ frame, shell, onNode, onFocusTask }: MountProps) {
  const [failure, setFailure] = useState<string | null>(null);
  const open = panelOpen(frame.view);
  const commands = paletteCommands(
    {
      toggleSidebar: shell.toggleSidebar,
      toggleDock: shell.toggleDock,
      closeTab: open ? frame.onClosePanel : undefined,
      toggleMaximize: open ? shell.toggleMaximize : undefined,
      focusTask: onFocusTask,
      rescan:
        !replayBuild && frame.onStartAgent
          ? () => {
              setFailure(null);
              void createCommandClient()
                .rescanGraph()
                .catch(() => {
                  setFailure("The graph could not rescan. Try Rescan graph again.");
                });
            }
          : undefined,
    },
    shell.maximized,
  );
  return (
    <>
      {failure && (
        <p role="alert" className="palette-failure">
          {failure}
        </p>
      )}
      {shell.palette && (
        <ConnectedPalette
          {...frame}
          commands={commands}
          onNode={onNode}
          returnFocus={shell.palette.returnFocus}
          onClose={shell.closePalette}
        />
      )}
    </>
  );
}

function ConnectedPalette({
  state,
  commands,
  onNode,
  returnFocus,
  onClose,
}: FrameProps & {
  commands: PaletteCommand[];
  onNode: (id: string) => void;
  returnFocus: HTMLElement | null;
  onClose: () => void;
}) {
  const navigation = useFilesNavigation();
  const listing = useTreeListing(
    navigation ?? {
      state,
      openFile: () => undefined,
      expanded: new Set<string>(),
      toggle: () => undefined,
    },
  );
  const fileNotice = listing.failure
    ? "Files could not refresh."
    : !listing.tree
      ? "Loading files…"
      : listing.tree.truncated
        ? "File listing is truncated; more files are omitted."
        : undefined;
  return (
    <Palette
      items={paletteItems(
        state,
        listing.tree?.files.map((file) => file.path) ?? [],
        onNode,
        (path) => navigation?.openFile(path),
        commands,
      )}
      returnFocus={returnFocus}
      onClose={onClose}
      fileNotice={fileNotice}
      onRetry={listing.failure ? listing.retry : undefined}
    />
  );
}
