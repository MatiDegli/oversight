import type { FrameProps } from "../Frame";

/** Only person navigation dismisses the workflow; replay reconciliation is not navigation. */
export function workflowNavigation(props: FrameProps, dismiss: () => void): FrameProps {
  function navigate<A extends unknown[]>(callback: (...args: A) => void): (...args: A) => void;
  function navigate<A extends unknown[]>(
    callback: ((...args: A) => void) | undefined,
  ): ((...args: A) => void) | undefined;
  function navigate<A extends unknown[]>(callback: ((...args: A) => void) | undefined) {
    return callback
      ? (...args: A) => {
          dismiss();
          callback(...args);
        }
      : undefined;
  }
  return {
    ...props,
    onNodeTap: navigate(props.onNodeTap),
    onClosePanel: navigate(props.onClosePanel),
    onOpenChange: navigate(props.onOpenChange),
    onOpenConversation: navigate(props.onOpenConversation),
    onReview: navigate(props.onReview),
    onOpenQueue: navigate(props.onOpenQueue),
    onOpenSettings: navigate(props.onOpenSettings),
    onOpenUsage: navigate(props.onOpenUsage),
    onViewMoment: navigate(props.onViewMoment),
    onViewHighlight: navigate(props.onViewHighlight),
    onViewFile: navigate(props.onViewFile),
    onTabAction: props.onTabAction
      ? (action) => {
          if (action.type !== "clear-reference") dismiss();
          props.onTabAction?.(action);
        }
      : undefined,
  };
}
