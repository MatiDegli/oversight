import "./usage.css";
import type { UsageView } from "@contracts";
import { CircleAlert, Timer } from "lucide-react";
import { useEffect, useState } from "react";
import type { State } from "../../state";
import { readUsage } from "./read";
import type { Grouping } from "./model";
import { UsageContent } from "./UsageContent";

export default function UsagePanel({ state, recorded }: { state: State; recorded?: boolean }) {
  const runId = state.run?.run_id;
  const [retry, setRetry] = useState(0);
  const [chosen, choose] = useState<Grouping>();
  const [result, setResult] = useState<{ data?: UsageView; error?: string }>();
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
      setResult({ error: "Usage took too long to load. Try again." });
    }, 15_000);
    void readUsage(runId, controller.signal).then(
      (data) => {
        clearTimeout(timeout);
        if (!controller.signal.aborted) setResult({ data });
      },
      (error: unknown) => {
        clearTimeout(timeout);
        if (!controller.signal.aborted)
          setResult({
            error: error instanceof Error ? error.message : "Usage could not be read. Try again.",
          });
      },
    );
    return () => {
      clearTimeout(timeout);
      controller.abort();
    };
  }, [runId, state.lastSeq, retry]);
  if (!runId)
    return (
      <p role="status" className="flex gap-2 p-4 text-muted">
        <Timer aria-hidden="true" size={16} />
        Usage is available when a run starts.
      </p>
    );
  if (result?.error)
    return (
      <div className="p-4">
        <p role="alert" className="flex gap-2 text-muted">
          <CircleAlert aria-hidden="true" size={16} />
          {result.error}
        </p>
        <button
          type="button"
          className="mt-3 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
          onClick={() => {
            setResult(undefined);
            setRetry((n) => n + 1);
          }}
        >
          Retry usage
        </button>
      </div>
    );
  if (!result?.data || result.data.run_id !== runId)
    return (
      <p role="status" className="flex gap-2 p-4 text-muted">
        <Timer aria-hidden="true" size={16} />
        Loading usage…
      </p>
    );
  return (
    <UsageContent
      data={result.data}
      state={state}
      recorded={recorded}
      chosen={chosen}
      choose={choose}
    />
  );
}
