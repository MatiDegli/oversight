import { useEffect, useState } from "react";

import { GraphView } from "../graph";
import { replayBuild, replaySource } from "../transport";
import { startAppSession } from "./session";

export function App() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    return startAppSession(document.getElementById("root"), (failure) => {
      setError(
        failure === null ? null : failure instanceof Error ? failure.message : "Transport failed",
      );
    });
  }, []);
  return (
    <main className="relative h-full bg-background">
      <GraphView />
      {(replayBuild || error) && (
        <div className="absolute left-4 top-4 text-muted">
          {replayBuild && <p>{replaySource.label}</p>}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </main>
  );
}
