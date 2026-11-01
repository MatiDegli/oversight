import { lazy } from "react";
import { loadReviewPanel } from "../../review";
import type { FrameProps } from "../Frame";
import { LazyPanel } from "./LazyPanel";
import { ReviewFrame } from "./PanelFallbacks";

const ReviewPanel = lazy(() => loadReviewPanel().then((m) => ({ default: m.ReviewPanel })));

/** One lazy adapter, key and capability path in both layouts. */
export function ReviewFocus(props: FrameProps & { changeId: string }) {
  const { state, changeId, onClosePanel } = props;
  return (
    <LazyPanel
      key={`review:${changeId}`}
      frame={<ReviewFrame state={state} changeId={changeId} onClose={onClosePanel} />}
    >
      <ReviewPanel
        key={changeId}
        state={state}
        changeId={changeId}
        attempt={props.attempts?.[changeId]}
        revertAttempt={props.revertAttempts?.[changeId]}
        onRevert={props.onRevert}
        files={props.files?.readOnly ? undefined : props.files}
        onDecide={props.onDecide}
        replayOf={props.replayOf}
        onClose={onClosePanel}
      />
    </LazyPanel>
  );
}
