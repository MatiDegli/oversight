import { useEffect, useState } from "react";

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
    <main className="h-full bg-background">
      {replayBuild && <p>{replaySource.label}</p>}
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
