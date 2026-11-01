import { CircleAlert, Timer } from "lucide-react";
import { Component, lazy, Suspense, type ReactNode } from "react";
import type { State } from "../../state";

const UsagePanel = lazy(() => import("./UsagePanel"));
class UsageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? (
      <p role="alert" className="flex gap-2 p-4 text-muted">
        <CircleAlert aria-hidden="true" size={16} />
        Usage could not load. Reload the page to try again.
      </p>
    ) : (
      this.props.children
    );
  }
}
export function UsageMount(props: { state: State; recorded?: boolean }) {
  return (
    <UsageBoundary>
      <Suspense
        fallback={
          <p role="status" className="flex gap-2 p-4 text-muted">
            <Timer aria-hidden="true" size={16} />
            Loading usage…
          </p>
        }
      >
        <UsagePanel {...props} />
      </Suspense>
    </UsageBoundary>
  );
}
