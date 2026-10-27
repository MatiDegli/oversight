import { useLayoutEffect, useRef, useState } from "react";

import { StartAgentHere } from "../agents";
import type { State } from "../state";
import type { View } from "./view";

/** Expand beside the file; only the click requests focus, consumed when the input mounts. */
export function useFunctionTask(state: State, view: View, enabled: boolean) {
  const [expanded, setExpanded] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const input = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const element = input.current;
    if (focusRequest === 0 || !element) return;
    element.focus();
    element.setSelectionRange(element.value.length, element.value.length);
  }, [focusRequest]);
  const node = state.graph?.nodes.find((candidate) => candidate.id === view.selected);
  return {
    expanded: expanded && view.file !== null,
    inputRef: input,
    action:
      enabled && node && view.file?.path === node.path ? (
        <StartAgentHere
          node={node}
          state={state}
          onOpen={() => {
            setExpanded(true);
            setFocusRequest((request) => request + 1);
          }}
        />
      ) : null,
  };
}
