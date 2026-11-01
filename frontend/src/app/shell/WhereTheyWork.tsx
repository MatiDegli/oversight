import { Fragment } from "react";
import { Eye, File, Pencil } from "lucide-react";
import { agentSlotStyle } from "../../theme";
import { useFilesNavigation } from "../FilesContext";
import { selectWorkPresence, type WorkGroup } from "../workPresence";
import "./whereTheyWork.css";

function WorkFiles({ group }: { group: WorkGroup }) {
  const Icon = group.mode === "edit" ? Pencil : group.mode === "read" ? Eye : File;
  return group.files.map((file) => (
    <div className="work-file" key={file.path}>
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
            {range.start_line === null || range.end_line === null
              ? "Whole file"
              : range.start_line === range.end_line
                ? `line ${String(range.start_line)}`
                : `lines ${String(range.start_line)} to ${String(range.end_line)}`}
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
            style={agentSlotStyle(group.slot)}
            data-agent-id={group.agentId}
            data-mode={group.mode}
          >
            <h3>
              <span className="work-badge">{group.slot}</span>
              <span className="work-agent">
                Agent {group.slot}
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
            <WorkFiles group={group} />
          </section>
        </Fragment>
      ))}
      <p className="work-footer">Files only. The functions around them are on the graph.</p>
    </div>
  );
}
