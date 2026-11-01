import { TriangleAlert } from "lucide-react";
import { cloneElement, Component, Suspense } from "react";
import type { ErrorInfo, ReactElement, ReactNode } from "react";

/** Keep a failed optional chunk inside its panel, leaving the graph available. */
export class LazyPanel extends Component<
  { frame: ReactElement<{ children?: ReactNode }>; children: ReactNode },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }
  override render() {
    return this.state.failed ? (
      cloneElement(
        this.props.frame,
        {},
        <p role="alert" className="flex items-start gap-2 p-4 text-sm text-muted">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          This panel could not load. Reload the page to try again.
        </p>,
      )
    ) : (
      <Suspense fallback={this.props.frame}>{this.props.children}</Suspense>
    );
  }
}
