import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import type { TreeResponse } from "@contracts";
import type { State } from "../state";

interface FilesNavigation {
  state: State;
  loadTree?: (signal: AbortSignal) => Promise<TreeResponse>;
  openFile: (path: string) => void;
  expanded: ReadonlySet<string>;
  toggle: (path: string) => void;
}
const FilesContext = createContext<FilesNavigation | null>(null);
export const useFilesNavigation = () => useContext(FilesContext);

/** Expansion belongs to the page, so collapsing the sidebar preserves it. */
export function FilesProvider({
  children,
  ...value
}: Omit<FilesNavigation, "expanded" | "toggle"> & { children: ReactNode }) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set(["shop", "tests"]));
  return (
    <FilesContext
      value={{
        ...value,
        expanded,
        toggle: (path) => {
          setExpanded((current) => {
            const next = new Set(current);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
          });
        },
      }}
    >
      {children}
    </FilesContext>
  );
}
