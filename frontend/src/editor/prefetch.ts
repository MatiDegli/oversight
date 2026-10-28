import { loadEditor } from "./loader";

type ConnectionNavigator = Navigator & { connection?: { saveData?: boolean } };
const savingData = () => (navigator as ConnectionNavigator).connection?.saveData === true;

/** Called after the first graph frame; never mounts a view or takes focus. */
export function prefetchEditorWhenIdle(): () => void {
  if (savingData()) return () => undefined;
  let active = true;
  const prefetch = () => {
    if (!active) return;
    active = false;
    if (!savingData()) void loadEditor().catch(() => undefined);
  };
  if (typeof window.requestIdleCallback === "function") {
    const handle = window.requestIdleCallback(prefetch, { timeout: 2_000 });
    return () => {
      active = false;
      window.cancelIdleCallback(handle);
    };
  }
  const handle = window.setTimeout(prefetch, 200);
  return () => {
    active = false;
    window.clearTimeout(handle);
  };
}
