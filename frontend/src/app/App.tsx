import { useEffect, useState } from "react";
import { useStore } from "zustand";

import { stateStore } from "../state";
import { replayBuild } from "../transport";
import { startAppSession } from "./session";

export function App() {
  const seq = useStore(stateStore, ({ state }) => state.lastSeq);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    return startAppSession(document.getElementById("root"), (failure) => {
      setError(failure instanceof Error ? failure.message : "Transport failed");
    });
  }, []);
  return (
    <main className="h-full bg-background" data-seq={seq}>
      {replayBuild && <p>Replay · hand-written demo fixture</p>}
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
