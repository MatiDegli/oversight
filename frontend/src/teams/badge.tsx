import type { AgentPresentation } from "./presentation";
import { agentLook } from "../theme";

/** Neutral actors stay explicit; paint is supplied only by the shared presentation. */
export const presentationLook = (identity: AgentPresentation | undefined) =>
  identity
    ? agentLook[identity.hue]
    : {
        family: undefined,
        text: undefined,
        tint: "bg-raised text-text",
        border: "border-border",
        fill: "bg-muted",
        swatch: "bg-raised text-text",
        textClass: "text-text",
      };

export function AgentBadge({ identity }: { identity: AgentPresentation | undefined }) {
  if (!identity) return <span>Agent unknown</span>;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-label border px-1.5 font-bold ${presentationLook(identity).border} ${presentationLook(identity).tint}`}
      title={identity.planId ?? undefined}
      aria-label={`Agent ${identity.label}`}
    >
      {identity.label}
    </span>
  );
}
