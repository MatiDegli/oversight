import type { State } from "../state";
import { getActiveTheme } from "../theme";

import { graphElements } from "./elements";
import { nodeGeometry } from "./geometry";
import type { GraphViewProps } from "./GraphView";

/** Fold controls reuse one elements snapshot until state, scope or theme changes. */
export function elementSnapshot() {
  let previous: readonly unknown[] = [];
  let elements: ReturnType<typeof graphElements> = [];
  return (state: State, props: GraphViewProps) => {
    const cone = props.cone ?? props.liveCone;
    const preserveReads = props.field?.mode === "live-diff";
    const theme = getActiveTheme();
    const inputs = [
      state,
      props.litIds,
      cone,
      props.measured,
      props.merging,
      preserveReads,
      theme,
      JSON.stringify(nodeGeometry(theme)),
    ];
    if (inputs.some((value, index) => value !== previous[index])) {
      elements = graphElements(
        state,
        false,
        props.litIds ? new Set(props.litIds) : null,
        cone,
        preserveReads,
        props.cone ? props.measured : null,
        props.merging,
      );
      previous = inputs;
    }
    return elements;
  };
}
