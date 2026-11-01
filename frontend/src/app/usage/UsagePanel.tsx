import { Timer } from "lucide-react";
import type { State } from "../../state";
export default function UsagePanel({ state }: { state: State; recorded?: boolean }) {
  return (
    <p role="status">
      <Timer aria-hidden="true" size={16} />
      Loading usage for {state.run?.run_id ?? "the next run"}…
    </p>
  );
}
