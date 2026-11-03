import { Fragment } from "react";
import { Eye, File, Pencil } from "lucide-react";
import { AgentBadge } from "../../teams/badge";
import { presentationStyle } from "../../teams/presentation";
import { useFilesNavigation } from "../FilesContext";
import { selectWorkPresence, type WorkFile, type WorkGroup } from "../workPresence";
import "./whereTheyWork.css";

function workDescription(group: WorkGroup) {
  return group.mode === "pending"
    ? group.conflict === "merge"
      ? "merge conflicts with main"
      : group.conflict === "tests"
        ? "combined tests failed"
        : "change waiting for you"
    : group.mode === "edit"
      ? "Editing"
      : "Reading";
}
function rangeText(range: WorkFile["ranges"][number]) {
  return range.start_line === null
    ? "Whole file"
    : range.end_line === null
      ? `from line ${String(range.start_line)}`
      : range.start_line === range.end_line
        ? `line ${String(range.start_line)}`
        : `lines ${String(range.start_line)} to ${String(range.end_line)}`;
}
function WorkFiles({ group, openFile }: { group: WorkGroup; openFile: (path: string) => void }) {
  const Icon = group.mode === "edit" ? Pencil : group.mode === "read" ? Eye : File;
  return group.files.map((file) => (
    <div className="work-file" key={file.path}>
      <button
        className="work-chip"
        type="button"
        onClick={() => { openFile(file.path); }}
        aria-label={`Open ${file.path} · Agent ${group.presentation?.label ?? "unknown"} · ${workDescription(group)}`}
        aria-description={[
          ...file.functions,
          ...file.ranges.map(rangeText),
          group.mode === "pending"
            ? `Added ${String(file.lines_added)}; removed ${String(file.lines_removed)}`
            : null,
        ]
          .filter(Boolean)
          .join("; ")}
      >
        <span>
          Agent <AgentBadge identity={group.presentation} />
        </span>
        <span className="work-chip-path">{file.path}</span>
        <span className="work-chip-mode">
          <Icon aria-hidden="true" size={15} />
          {workDescription(group)}
        </span>
      </button>
      <Icon aria-hidden="true" size={15} />
      <div className="work-file-content">
        <div className="work-path">
          {file.path.split("/").map((part, i, parts) => (
            <Fragment key={i}>
              {part}
              {i < parts.length - 1 && (
                <>
                  /<wbr />
                </>
              )}
            </Fragment>
          ))}
        </div>
        {file.functions.map((name) => (
          <div className="work-function" key={name}>
            {name}
          </div>
        ))}
        {file.ranges.map((range, i) => (
          <div className="work-range" key={i}>
            {rangeText(range)}
          </div>
        ))}
        {group.mode === "pending" && (
          <div className="work-statistics">
            <span className="text-added">+{file.lines_added}</span>{" "}
            <span className="text-removed">−{file.lines_removed}</span>
          </div>
        )}
      </div>
    </div>
  ));
}

export function WhereTheyWork() {
  const navigation = useFilesNavigation();
  if (!navigation) return null;
  const { hasAgents, current, pending } = selectWorkPresence(navigation.state);
  return (
    <div className="where-they-work">
      {!current.length && (
        <p className="work-empty">
          {hasAgents
            ? "Nobody is reading or editing now."
            : "Nobody is reading or editing yet. When an agent opens a file, it shows here."}
        </p>
      )}
      {[...current, ...pending].map((group, i) => (
        <Fragment key={`${group.agentId}:${group.mode}:${group.changeId ?? ""}`}>
          {i > 0 && <hr className="work-divider" />}
          <section
            className="work-group"
            style={presentationStyle(group.presentation)}
            data-agent-id={group.agentId}
            data-mode={group.mode}
          >
            <h3>
              <span className="work-badge">{group.presentation?.label ?? "?"}</span>
              <span className="work-agent">
                Agent {group.presentation?.label ?? "?"}
                {group.mode === "pending" &&
                  (group.conflict === "merge"
                    ? " · merge conflicts with main"
                    : group.conflict === "tests"
                      ? " · combined tests failed"
                      : " · change waiting for you")}
              </span>
              {group.mode !== "pending" && (
                <span className="work-mode">
                  {group.mode === "edit" ? (
                    <Pencil size={14} aria-hidden="true" />
                  ) : (
                    <Eye size={14} aria-hidden="true" />
                  )}
                  {group.mode === "edit" ? "Editing" : "Reading"}
                </span>
              )}
            </h3>
            <WorkFiles group={group} openFile={navigation.openFile} />
          </section>
        </Fragment>
      ))}
      <p className="work-footer">
        Files and observed functions. Explore their connections on the graph.
      </p>
    </div>
  );
}
