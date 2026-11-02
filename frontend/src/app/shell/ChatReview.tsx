import { useEffect, useId, useState, type ReactNode } from "react";
import type { FrameProps } from "../Frame";
import { GraphLink } from "./ChatChangeCard";
import { ReviewFocus } from "./ReviewFocus";
import "./chatReview.css";

/** Stable slots keep the single conversation and review alive through both layout switches. */
export function ChatReview({
  chat,
  agentId,
  conversation,
  onGraph,
  ...props
}: FrameProps & {
  chat?: boolean;
  agentId?: string;
  conversation: ReactNode;
  onGraph?: (id: string) => void;
}) {
  const id = useId();
  const review = props.view.review;
  const [threadVisited, setThreadVisited] = useState(chat || !review);
  if (chat && !threadVisited) setThreadVisited(true);
  const [selection, setSelection] = useState({ review, tab: "review" });
  if (selection.review !== review) setSelection({ review, tab: "review" });
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 1500px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1500px)");
    const resize = () => {
      setNarrow(media.matches);
    };
    media.addEventListener("change", resize);
    return () => {
      media.removeEventListener("change", resize);
    };
  }, []);
  const tabs = Boolean(chat && review && narrow);
  const slot = agentId ? props.state.agents[agentId]?.slot : undefined;
  const change = review ? props.state.changes[review] : undefined;
  const reviewState =
    change?.resolution?.merge === "merged"
      ? "merged"
      : change?.status === "ready_for_review"
        ? "ready"
        : (change?.status.replaceAll("_", " ") ?? "unavailable");
  const hideConversation =
    !agentId || (review !== null && (!chat || (tabs && selection.tab !== "conversation")));
  const hideReview = review === null || (tabs && selection.tab !== "review");
  return (
    <div
      className={`chat-review-composition ${chat ? "in-chat" : ""} ${review ? "has-review" : ""}`}
    >
      {tabs && (
        <div role="tablist" aria-label="Conversation and review" className="chat-review-tabs">
          {(["conversation", "review"] as const).map((tab) => (
            <button
              type="button"
              role="tab"
              key={tab}
              id={`${id}-${tab}-tab`}
              aria-controls={`${id}-${tab}`}
              aria-selected={selection.tab === tab}
              tabIndex={selection.tab === tab ? 0 : -1}
              onClick={() => {
                setSelection({ review, tab });
              }}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next =
                  event.key === "Home"
                    ? "conversation"
                    : event.key === "End"
                      ? "review"
                      : tab === "review"
                        ? "conversation"
                        : "review";
                setSelection({ review, tab: next });
                document.getElementById(`${id}-${next}-tab`)?.focus();
              }}
            >
              {tab === "conversation"
                ? `Agent ${slot ?? "?"} · conversation`
                : `Review · ${reviewState}`}
            </button>
          ))}
        </div>
      )}
      <div
        id={`${id}-conversation`}
        role={tabs ? "tabpanel" : undefined}
        aria-labelledby={tabs ? `${id}-conversation-tab` : undefined}
        className="chat-review-conversation"
        hidden={hideConversation}
        inert={hideConversation}
      >
        {threadVisited ? conversation : null}
      </div>
      <div
        id={`${id}-review`}
        role={tabs ? "tabpanel" : undefined}
        aria-labelledby={tabs ? `${id}-review-tab` : undefined}
        className="chat-review-panel"
        hidden={hideReview}
        inert={hideReview}
      >
        {review && (
          <>
            {chat && (
              <div className="chat-review-graph">
                <GraphLink
                  onClick={
                    onGraph
                      ? () => {
                          onGraph(review);
                        }
                      : undefined
                  }
                />
              </div>
            )}
            <ReviewFocus {...props} changeId={review} />
          </>
        )}
      </div>
    </div>
  );
}
