import type { ReactNode } from "react";
import type { PreviewStartupReport, PreviewStartupResult } from "./previewStartup";
import "./previewStartup.css";

export function PreviewStartupPanel({
  result,
  headingId,
}: {
  result: PreviewStartupResult;
  headingId: string;
}) {
  return (
    <section className="preview-startup" aria-labelledby={headingId}>
      <h2 id={headingId}>Preview startup</h2>
      {result.kind === "valid" ? (
        <StartupValues report={result.report} />
      ) : (
        <p className="preview-startup-notice">
          {result.kind === "missing"
            ? "Not enabled on this server."
            : "The preview startup report is invalid, so reviewed-app previews stay off."}
        </p>
      )}
    </section>
  );
}

function StartupValues({ report }: { report: PreviewStartupReport }) {
  const rows: readonly [string, ReactNode][] = [
    ["Version", report.version],
    [
      "Commands (argv)",
      <ol className="preview-startup-commands">
        {report.argv.map((command, index) => (
          <li key={index}>
            <div className="preview-startup-arguments">
              {command.map((argument, index) => (
                <code className="preview-startup-token" key={index}>
                  {argument}
                </code>
              ))}
            </div>
          </li>
        ))}
      </ol>,
    ],
    ["Working directory", <code>{report.cwd}</code>],
    ["Selection reason", report.reason],
    [
      "Prepared dependencies",
      <ul className="preview-startup-dependencies">
        {report.dependencies.map((dependency, index) => (
          <li key={index}>{dependency}</li>
        ))}
      </ul>,
    ],
    ["Limits", report.limits],
    ["Platform residual", report.platform_residual],
    ["Build log", <code>{report.build_log}</code>],
    ["Enable hint", report.enable_hint],
    ["Agents", report.agents_label],
  ];
  return (
    <dl className="preview-startup-values">
      {rows.map(([label, value]) => (
        <div className="preview-startup-row" key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
