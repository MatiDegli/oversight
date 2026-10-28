import { agentSlotStyle } from "../../../theme";
import { useLayoutEffect, useMemo, useRef } from "react";
import { Check, CircleAlert } from "lucide-react";
import type { State } from "../../../state";
import { agentLetter, duration, LOG_LIMIT, selectActivity } from "./selectors";

const clock = new Intl.DateTimeFormat("en-GB", {
  timeZone: "America/Argentina/Buenos_Aires",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function Activity({ state }: { state: State }) {
  const { entries, omitted } = useMemo(() => selectActivity(state), [state]);
  const viewport = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useLayoutEffect(() => {
    if (following.current && viewport.current) {
      viewport.current.scrollTop = viewport.current.scrollHeight;
    }
  }, [entries]);
  if (!entries.length)
    return <p className="dock-empty">The run’s activity log will appear here.</p>;
  return (
    <>
      <p className="dock-note">
        Limit <span className="dock-number">{LOG_LIMIT}</span> entries; omitted:{" "}
        <span className="dock-number">{omitted}</span>.
      </p>
      <div
        ref={viewport}
        className="dock-activity-scroll"
        tabIndex={0}
        aria-label="Activity entries"
        onScroll={(event) => {
          const node = event.currentTarget;
          following.current = node.scrollHeight - node.clientHeight - node.scrollTop <= 2;
        }}
      >
        <ol aria-label="Run activity">
          {entries.map((entry) => {
            const letter = agentLetter(state, entry.agentId);
            const Icon = entry.failed ? CircleAlert : Check;
            return (
              <li key={entry.id} className="dock-activity-row" data-entry={entry.id}>
                <span
                  className="dock-letter"
                  data-slot={letter}
                  style={agentSlotStyle(
                    entry.agentId ? state.agents[entry.agentId]?.slot : undefined,
                  )}
                  aria-label={entry.agentId ? `Agent ${letter}` : "Run"}
                >
                  {letter}
                </span>
                <Icon aria-hidden="true" />
                <div className="dock-entry-copy">
                  <span>{entry.title}</span>
                  {entry.summary && <p>{entry.summary}</p>}
                </div>
                {entry.duration !== null && (
                  <span className="dock-number">{duration(entry.duration)}</span>
                )}
                {entry.time ? (
                  <time className="dock-number" dateTime={entry.time}>
                    {clock.format(new Date(entry.time))} ART
                  </time>
                ) : (
                  <span className="dock-note">No time</span>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}
