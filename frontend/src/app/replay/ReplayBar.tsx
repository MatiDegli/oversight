import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { Check, Pause, Play, RotateCcw, X } from "lucide-react";
import { stateStore } from "../../state";
import { agentSlotStyle } from "../../theme";
import type { ReplayPlayer } from "../../transport/replayPlayer";
import type { View } from "../view";
import { reconcileReplayView, replayMarks, replayTime } from "./model";
import "./replay.css";

export default function ReplayBar({
  player,
  onCommit,
}: {
  player: ReplayPlayer;
  onCommit: (reconcile: (view: View) => View) => void;
}) {
  const snapshot = useSyncExternalStore(player.subscribe, player.getSnapshot);
  const { position, duration, status, speed, revision } = snapshot;
  const marks = useMemo(
    () => replayMarks(snapshot.events, snapshot.offsets),
    [snapshot.events, snapshot.offsets],
  );
  const [preview, setPreview] = useState<number | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const drag = useRef<number | null>(null);
  const mark = marks.find((candidate) => candidate.id === (hovered ?? focused));
  const tooltip = dismissed ? undefined : mark;
  const ghost = tooltip?.offset ?? preview;
  useEffect(() => {
    const state = stateStore.getState().state;
    onCommit((view) => reconcileReplayView(view, state));
  }, [revision, onCommit]);
  const pointerPosition = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(duration, ((event.clientX - box.left) / box.width) * duration));
  };
  const keyboardSeek = (event: KeyboardEvent) => {
    const step = event.shiftKey ? 5000 : 1000;
    const target = {
      ArrowLeft: position - step,
      ArrowRight: position + step,
      Home: 0,
      End: duration,
    }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    player.seek(target);
  };
  const percent = (offset: number) => `${duration ? (offset / duration) * 100 : 0}%`;
  return (
    <section className="replay-bar" aria-label="Replay controls">
      <button
        className={`replay-button ${status !== "ended" ? "is-primary" : ""}`}
        aria-label={status === "playing" ? "Pause" : status === "ended" ? "Play again" : "Play"}
        onClick={status === "playing" ? player.pause : player.play}
      >
        {status === "playing" ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
      </button>
      <button
        className={`replay-button replay-restart ${status === "ended" ? "is-primary" : ""}`}
        aria-label="Restart"
        onClick={player.restart}
      >
        <RotateCcw aria-hidden="true" />
        <span>Restart</span>
      </button>
      <div className="replay-time">
        <div>
          {replayTime(position)} <span>/ {replayTime(duration)}</span>
        </div>
        <small>
          {status === "playing" ? "Playing" : status === "ended" ? "End of recording" : "Paused"}
        </small>
      </div>
      <div className="replay-strip">
        <div
          className="replay-slider"
          role="slider"
          tabIndex={0}
          aria-label="Position in the recording"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={Math.floor(position)}
          aria-valuetext={`${replayTime(position)} of ${replayTime(duration)}`}
          onKeyDown={keyboardSeek}
          onPointerDown={(event) => {
            if (drag.current !== null || event.button !== 0) return;
            drag.current = event.pointerId;
            event.currentTarget.setPointerCapture(event.pointerId);
            setPreview(pointerPosition(event));
          }}
          onPointerMove={(event) => {
            if (drag.current === null || drag.current === event.pointerId)
              setPreview(pointerPosition(event));
          }}
          onPointerUp={(event) => {
            if (drag.current !== event.pointerId) return;
            drag.current = null;
            event.currentTarget.releasePointerCapture(event.pointerId);
            player.seek(pointerPosition(event));
            setPreview(null);
          }}
          onPointerCancel={() => {
            drag.current = null;
            setPreview(null);
          }}
          onLostPointerCapture={() => {
            drag.current = null;
            setPreview(null);
          }}
          onBlur={() => {
            drag.current = null;
            setPreview(null);
          }}
          onPointerLeave={() => {
            if (drag.current === null) setPreview(null);
          }}
        >
          <div className="replay-track">
            <div style={{ width: percent(position) }} />
          </div>
          <div className="replay-knob" style={{ left: percent(position) }} />
        </div>
        {ghost !== null && <div className="replay-ghost" style={{ left: percent(ghost) }} />}
        {marks.map((item) => (
          <button
            key={item.id}
            className="replay-mark"
            data-kind={item.kind}
            style={{ left: percent(item.offset), ...agentSlotStyle(item.slot) }}
            aria-label={`${item.title}, ${replayTime(item.offset)}`}
            aria-describedby={tooltip?.id === item.id ? "replay-tooltip" : undefined}
            onClick={() => player.seek(item.offset)}
            onMouseEnter={() => {
              setHovered(item.id);
              setDismissed(false);
            }}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => {
              setFocused(item.id);
              setDismissed(false);
            }}
            onBlur={() => setFocused(null)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                setDismissed(true);
              }
            }}
          >
            <span>{item.letters}</span>
            <i aria-hidden="true" />
          </button>
        ))}
        {tooltip && (
          <div
            role="tooltip"
            id="replay-tooltip"
            className="replay-tooltip"
            style={{
              left: `clamp(0px, calc(${percent(tooltip.offset)} - 140px), max(0px, 100% - 300px))`,
            }}
          >
            <div>
              <strong>{tooltip.title}</strong>
              <time>{replayTime(tooltip.offset)}</time>
            </div>
            {tooltip.proposal && (
              <p>
                <span
                  className={
                    tooltip.proposal.tests.outcome === "passed" ? "text-passed" : "text-failed-text"
                  }
                >
                  {tooltip.proposal.tests.outcome === "passed" ? (
                    <Check aria-hidden="true" />
                  ) : (
                    <X aria-hidden="true" />
                  )}
                  {tooltip.proposal.tests.passed} of{" "}
                  {tooltip.proposal.tests.passed + tooltip.proposal.tests.failed}{" "}
                  {tooltip.proposal.tests.outcome === "passed"
                    ? "pass"
                    : tooltip.proposal.tests.outcome}
                </span>{" "}
                · <span className="text-added">+{tooltip.proposal.lines_added}</span>{" "}
                <span className="text-removed">−{tooltip.proposal.lines_removed}</span> in{" "}
                {tooltip.proposal.files.length} files
              </p>
            )}
            {tooltip.hash && <p className="replay-hash">{tooltip.hash.slice(0, 7)}</p>}
          </div>
        )}
      </div>
      <div className="replay-speed" aria-label="Playback speed">
        {([1, 2, 4] as const).map((rate) => (
          <button key={rate} aria-pressed={speed === rate} onClick={() => player.setSpeed(rate)}>
            {rate}×
          </button>
        ))}
      </div>
    </section>
  );
}
