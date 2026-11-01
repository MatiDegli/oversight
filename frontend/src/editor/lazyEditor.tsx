import { TriangleAlert } from "lucide-react";
import { Component, lazy, Suspense, useState } from "react";
import type { ComponentType, ErrorInfo, ReactNode } from "react";
import { loadedEditor, loadEditor } from "./loader";
import type { EditorImplementation } from "./loader";

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
  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
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
  select: (module: EditorImplementation) => ComponentType<Props>,
  filePanel = false,
) {
  const Deferred = lazy(() => loadEditor().then((module) => ({ default: select(module) })));
  return function DeferredEditor({
    loadingFallback,
    ...props
  }: Props & { loadingFallback?: ReactNode }) {
    // Fix the identity for this mount: a cold open must not remount when the cache fills.
    const [View] = useState(() => {
      const loaded = loadedEditor();
      return loaded ? select(loaded) : Deferred;
    });
    return (
      <EditorBoundary filePanel={filePanel}>
        <Suspense
          fallback={loadingFallback ?? <EditorFallback failed={false} filePanel={filePanel} />}
        >
          <View {...(props as Props)} />
        </Suspense>
      </EditorBoundary>
    );
  };
}
