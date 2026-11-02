import { useLayoutEffect, useRef } from "react";

export function useRelocatedDetails() {
  const root = useRef<HTMLElement>(null);
  const detailsNodes = useRef<HTMLDetailsElement[]>([]);
  const focusedDetail = useRef<{ element: HTMLElement; index: number } | null>(null);
  useLayoutEffect(() => {
    const element = root.current;
    const focus = (event: Event) => {
      const target = event.target;
      const nodes = Array.from(element?.querySelectorAll<HTMLElement>(".review-details *") ?? []);
      focusedDetail.current =
        target instanceof HTMLElement && nodes.includes(target)
          ? { element: target, index: nodes.indexOf(target) }
          : null;
    };
    const blur = (event: FocusEvent) => {
      const target = event.target;
      // Chromium emits focusout before removeChild detaches a relocated summary.
      // Check after that removal so only a blur from a still-mounted control clears it.
      queueMicrotask(() => {
        if (
          target instanceof HTMLElement &&
          target.isConnected &&
          focusedDetail.current?.element === target
        )
          focusedDetail.current = null;
      });
    };
    element?.addEventListener("focusin", focus);
    element?.addEventListener("focusout", blur);
    return () => {
      element?.removeEventListener("focusin", focus);
      element?.removeEventListener("focusout", blur);
    };
  }, []);
  useLayoutEffect(() => {
    const next = Array.from(
      root.current?.querySelectorAll<HTMLDetailsElement>(".review-details details") ?? [],
    );
    // Detached native nodes retain their open state even before queued toggle events fire.
    if (detailsNodes.current.length && detailsNodes.current[0] !== next[0])
      next.forEach((details, i) => {
        details.open = detailsNodes.current[i]?.open ?? false;
      });
    detailsNodes.current = next;
    const previous = focusedDetail.current;
    if (previous && !previous.element.isConnected && document.activeElement === document.body) {
      const replacement = root.current
        ?.querySelectorAll<HTMLElement>(".review-details *")
        .item(previous.index);
      replacement?.focus({ preventScroll: true });
    }
  });
  return root;
}
