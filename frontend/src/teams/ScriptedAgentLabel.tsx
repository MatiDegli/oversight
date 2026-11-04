import "./scriptedLabels.css";

export function ScriptedAgentLabel({ explain = false }: { explain?: boolean }) {
  const label = <span className="scripted-agent-label">Scripted demo agents</span>;
  if (!explain) return label;
  return (
    <span className="scripted-agent-provenance">
      {label}
      <span className="scripted-agent-explanation">
        Entered text does not determine scripted actions.
      </span>
    </span>
  );
}
