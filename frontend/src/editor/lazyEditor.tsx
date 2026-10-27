import { TriangleAlert } from "lucide-react";
import { Component, lazy, Suspense } from "react";
import type { ComponentType, ReactNode } from "react";

function EditorFallback({ failed, filePanel }: { failed: boolean; filePanel: boolean }) {
  const message = (
    <p role={failed ? "alert" : "status"} className="flex items-start gap-2 p-4 text-sm text-muted">
      {failed && <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />}
      {failed ? "The editor could not load. Reload the page to try again." : "Loading editor…"}
    </p>
  );
  return filePanel ? (
    <aside
      aria-label="File view"
      className="w-[min(var(--review-panel-width),46vw)] shrink-0 border-r border-divider bg-page text-text"
    >
      {message}
    </aside>
  ) : (
    message
  );
}

/** A failed local chunk stays in the focus panel, leaving the graph available. */
class EditorBoundary extends Component<
  { children: ReactNode; filePanel: boolean },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? (
      <EditorFallback failed filePanel={this.props.filePanel} />
    ) : (
      this.props.children
    );
  }
}

/** Each public component owns its Suspense boundary; importing the facade loads no editor. */
export function deferEditor<Props extends object>(
  load: () => Promise<{ default: ComponentType<Props> }>,
  filePanel = false,
) {
  const Deferred = lazy(load);
  return function DeferredEditor(props: Props) {
    return (
      <EditorBoundary filePanel={filePanel}>
        <Suspense fallback={<EditorFallback failed={false} filePanel={filePanel} />}>
          <Deferred {...props} />
        </Suspense>
      </EditorBoundary>
    );
  };
}
