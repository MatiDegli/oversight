import type { EditableSetting, EditableSettingName, SettingsView } from "@contracts";
import { useEffect, useId, useState } from "react";
import { useStore } from "zustand";
import { focusRing } from "./shared";
import { createSettingsEditor, sectionFor, type SettingsSection } from "./settingsEditor";

const fields: readonly [EditableSettingName, string, EditableSetting["kind"]][] = [
  ["META_API_KEY", "Meta API key", "secret"],
  ["BASETEN_API_KEY", "Baseten API key", "secret"],
  ["OVERSIGHT_CROSS_REVIEW", "Cross-review", "boolean"],
  ["OVERSIGHT_BUDGET_USD", "Meta budget (USD)", "number"],
  ["OVERSIGHT_BASETEN_BUDGET_USD", "Baseten budget (USD)", "number"],
  ["MUSE_SPARK_INPUT_USD_PER_M", "Muse Spark input (USD / 1M tokens)", "number"],
  ["MUSE_SPARK_OUTPUT_USD_PER_M", "Muse Spark output (USD / 1M tokens)", "number"],
  ["BASETEN_INPUT_USD_PER_M", "Baseten input (USD / 1M tokens)", "number"],
  ["BASETEN_OUTPUT_USD_PER_M", "Baseten output (USD / 1M tokens)", "number"],
  ["OVERSIGHT_SPEND_BRAKE_PERCENT", "Brake at (% of budget)", "number"],
  ["OVERSIGHT_MAX_AGENTS", "Agent cap (agents)", "number"],
  ["OVERSIGHT_MAX_STEPS", "Step limit (steps)", "number"],
];
const helpers: Partial<Record<EditableSettingName, string>> = {
  OVERSIGHT_BUDGET_USD: "Leave empty for no spending cap.",
  MUSE_SPARK_INPUT_USD_PER_M: "Leave empty to use 0.10",
  MUSE_SPARK_OUTPUT_USD_PER_M: "Leave empty to use 0.20",
  OVERSIGHT_MAX_STEPS: "Each agent stops after this many tool steps. 5 to 100; empty means 30.",
};
const states = { set: "Configured", missing: "Missing", invalid: "Invalid" };
const fieldClass = `mt-2 block min-h-10 w-full min-w-0 rounded-card border border-control-edge bg-field px-3 py-2 text-base text-text placeholder:text-faint read-only:text-muted ${focusRing}`;
const buttonClass = `min-h-10 rounded-control border border-control-edge px-4 py-2 text-sm font-semibold text-text aria-disabled:cursor-default aria-disabled:text-faint not-aria-disabled:hover:bg-page-raised ${focusRing}`;

type EditorStore = ReturnType<typeof createSettingsEditor>;
export function EditableSettings({
  live,
  workspace,
  store: supplied,
}: {
  live: boolean;
  workspace?: string;
  store?: EditorStore;
}) {
  const [local] = useState(createSettingsEditor);
  const store = supplied ?? local;
  const editor = useStore(store);
  useEffect(() => {
    if (live) void store.getState().load();
  }, [live, store]);
  const readOnly = !live || !editor.view || editor.pending !== null;
  return (
    <div className="mt-8 min-w-0 space-y-8">
      <RepositorySection workspace={workspace} live={live} apply={editor.view?.apply} />
      {!live && <p className="text-sm text-muted">Settings are read-only in a replay.</p>}
      {live && editor.loading && <p role="status">Loading settings…</p>}
      {editor.failure && editor.feedbackSection === null && (
        <div role="alert" className="space-y-2 text-sm wrap-anywhere text-text">
          <p>{editor.failure}</p>
          {!editor.view && (
            <button
              type="button"
              className={buttonClass}
              onClick={() => {
                void editor.load();
              }}
            >
              Try again
            </button>
          )}
        </div>
      )}
      {(["models", "limits"] as const).map((section) => (
        <SettingsSectionForm
          key={section}
          section={section}
          editor={editor}
          readOnly={readOnly}
          live={live}
        />
      ))}
    </div>
  );
}

/** The frontend cannot open the native picker; it points to the menu that does. */
function RepositorySection({
  workspace,
  live,
  apply,
}: {
  workspace?: string;
  live: boolean;
  apply?: SettingsView["apply"];
}) {
  const id = useId();
  const another = !live
    ? "In the Windows app, choose File > Open repository… to review your own repository."
    : apply === "desktop_reload"
      ? "To review another repository, choose File > Open repository… in the menu bar."
      : apply === "server_restart"
        ? "The repository is chosen when the server starts."
        : null;
  return (
    <section aria-labelledby={`${id}-repository`}>
      <h2 id={`${id}-repository`} className="text-(length:--title-size) font-bold">
        Repository
      </h2>
      <p className="mt-1 text-sm text-muted">
        {workspace ? (
          <>
            Agents edit a disposable copy of{" "}
            <span className="font-semibold text-text">{workspace}</span>, never your folder.
          </>
        ) : (
          "Agents edit a disposable copy, never your folder."
        )}
      </p>
      {another && <p className="mt-3 text-sm text-text">{another}</p>}
    </section>
  );
}

function SettingsSectionForm({
  section,
  editor,
  readOnly,
  live,
}: {
  section: SettingsSection;
  editor: ReturnType<EditorStore["getState"]>;
  readOnly: boolean;
  live: boolean;
}) {
  const id = useId();
  return (
    <section aria-labelledby={`${id}-${section}`}>
      <h2 id={`${id}-${section}`} className="text-(length:--title-size) font-bold">
        {section === "models" ? "Models and keys" : "Limits and budget"}
      </h2>
      <p className="mt-1 text-sm text-muted">
        {section === "models"
          ? "Keys stay in .env and are never returned. Changes apply on the next start."
          : "Spending limits and token prices in US dollars. Changes apply on the next start."}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!readOnly) void editor.save(section);
        }}
      >
        <div className="mt-4 grid min-w-0 gap-4 @sm:grid-cols-2">
          {fields
            .filter(([name]) => sectionFor(name) === section)
            .map(([name, label, kind]) => {
              const row = editor.view?.settings.find((setting) => setting.name === name);
              return (
                <SettingField
                  key={name}
                  name={name}
                  label={label}
                  kind={kind}
                  row={row}
                  clearing={kind === "secret" && editor.drafts[name] === ""}
                  onClear={() => {
                    editor.clearKey(name);
                  }}
                  onUndo={() => {
                    editor.undo(name);
                  }}
                  value={editor.drafts[name] ?? (kind === "secret" ? "" : (row?.value ?? ""))}
                  readOnly={readOnly || row?.overridden === true}
                  live={live}
                  onEdit={(value) => {
                    editor.edit(name, value);
                  }}
                />
              );
            })}
        </div>
        <div className="mt-4 flex items-end gap-3">
          <SectionFeedback section={section} editor={editor} />
          <button
            type="submit"
            className={`${buttonClass} shrink-0`}
            aria-disabled={readOnly || !hasChanges(editor.drafts, section)}
          >
            {editor.pending === section ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </section>
  );
}

function SectionFeedback({
  section,
  editor,
}: {
  section: SettingsSection;
  editor: ReturnType<EditorStore["getState"]>;
}) {
  const active = editor.feedbackSection === section;
  return (
    <div className="min-w-0 flex-1 space-y-2 text-sm wrap-anywhere">
      {active && editor.failure && (
        <p role="alert" className="text-text">
          {editor.failure}
        </p>
      )}
      <div role="status" className="text-muted">
        {editor.saved[section] && (
          <p>
            {section === "limits" && !editor.view?.restart_required
              ? "Saved. Applies to the next run."
              : "Saved."}
          </p>
        )}
        {editor.view?.restart_required && editor.restartSection === section && (
          <p className="text-text">
            {editor.view.apply === "desktop_reload"
              ? "Restart to apply: File > Reload settings."
              : "Restart the server to apply."}
          </p>
        )}
      </div>
    </div>
  );
}

function hasChanges(
  drafts: Partial<Record<EditableSettingName, string>>,
  section: SettingsSection,
) {
  return (Object.keys(drafts) as EditableSettingName[]).some(
    (name) => sectionFor(name) === section,
  );
}

function SettingField({
  name,
  label,
  kind,
  row,
  value,
  readOnly,
  live,
  onEdit,
  clearing,
  onClear,
  onUndo,
}: {
  clearing: boolean;
  onClear: () => void;
  onUndo: () => void;
  name: EditableSettingName;
  label: string;
  kind: EditableSetting["kind"];
  row: EditableSetting | undefined;
  value: string;
  readOnly: boolean;
  live: boolean;
  onEdit: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={`min-w-0 ${kind === "number" ? "" : "@sm:col-span-2"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-sm font-semibold">
          {label}
        </label>
        {kind === "secret" && (
          <span className="rounded-control border border-border px-2 py-0.5 text-sm text-muted">
            {row ? states[row.state] : live ? "Loading…" : "Unavailable in replay"}
          </span>
        )}
      </div>
      {row?.overridden && (
        <p id={`${id}-environment`} className="mt-1 text-sm text-muted">
          Set by the environment
        </p>
      )}
      <SettingControl
        id={id}
        name={name}
        kind={kind}
        row={row}
        value={value}
        readOnly={readOnly}
        onEdit={onEdit}
      />
      {kind === "secret" && row && row.state !== "missing" && live && (
        <KeyClearAction clearing={clearing} readOnly={readOnly} onClear={onClear} onUndo={onUndo} />
      )}
      {helpers[name] && (
        <p id={`${id}-default`} className="mt-1 text-sm text-muted">
          {helpers[name]}
        </p>
      )}
      {name === "OVERSIGHT_MAX_AGENTS" && (
        <p id={`${id}-default`} className="mt-1 text-sm text-muted">
          Leave empty to use 4 agents.
        </p>
      )}
      {name === "OVERSIGHT_SPEND_BRAKE_PERCENT" && (
        <p id={`${id}-default`} className="mt-1 text-sm text-muted">
          Leave empty to use 90%.
        </p>
      )}
      {row?.state === "invalid" && kind === "number" && (
        <p className="mt-1 text-sm text-muted">
          {name === "OVERSIGHT_SPEND_BRAKE_PERCENT"
            ? "Invalid value. Enter a whole number from 1 to 100."
            : name === "OVERSIGHT_MAX_AGENTS"
              ? "Invalid value. Enter a positive whole number."
              : name === "OVERSIGHT_MAX_STEPS"
                ? "Invalid value. Enter a whole number from 5 to 100."
                : "Invalid value. Enter a positive amount."}
        </p>
      )}
    </div>
  );
}

function KeyClearAction({
  clearing,
  readOnly,
  onClear,
  onUndo,
}: {
  clearing: boolean;
  readOnly: boolean;
  onClear: () => void;
  onUndo: () => void;
}) {
  return (
    <div className="mt-2">
      {clearing && (
        <p role="status" className="text-sm text-text">
          Key will be cleared when you save
        </p>
      )}
      <button
        type="button"
        aria-disabled={readOnly}
        className={`min-h-10 text-sm text-muted underline underline-offset-4 ${focusRing}`}
        onClick={() => {
          if (!readOnly) {
            if (clearing) onUndo();
            else onClear();
          }
        }}
      >
        {clearing ? "Undo" : "Clear key"}
      </button>
    </div>
  );
}

function SettingControl({
  id,
  name,
  kind,
  row,
  value,
  readOnly,
  onEdit,
}: {
  id: string;
  name: EditableSettingName;
  kind: EditableSetting["kind"];
  row: EditableSetting | undefined;
  value: string;
  readOnly: boolean;
  onEdit: (value: string) => void;
}) {
  return kind === "boolean" ? (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={value.toLowerCase() === "true"}
      aria-disabled={readOnly}
      aria-describedby={row?.overridden ? `${id}-environment` : undefined}
      className={`mt-2 ${buttonClass}`}
      onClick={() => {
        if (!readOnly) onEdit(value.toLowerCase() === "true" ? "false" : "true");
      }}
    >
      {value.toLowerCase() === "true" ? "On" : "Off"}
    </button>
  ) : (
    <input
      id={id}
      name={name}
      type={kind === "secret" ? "password" : "number"}
      value={value}
      readOnly={readOnly}
      autoComplete={kind === "secret" ? "new-password" : "off"}
      aria-describedby={
        [
          row?.overridden ? `${id}-environment` : "",
          helpers[name] ||
          name === "OVERSIGHT_MAX_AGENTS" ||
          name === "OVERSIGHT_SPEND_BRAKE_PERCENT"
            ? `${id}-default`
            : "",
        ]
          .filter(Boolean)
          .join(" ") || undefined
      }
      max={
        name === "OVERSIGHT_SPEND_BRAKE_PERCENT" || name === "OVERSIGHT_MAX_STEPS"
          ? "100"
          : undefined
      }
      min={
        kind === "number"
          ? name === "OVERSIGHT_MAX_STEPS"
            ? "5"
            : name === "OVERSIGHT_MAX_AGENTS" || name === "OVERSIGHT_SPEND_BRAKE_PERCENT"
              ? "1"
              : "0"
          : undefined
      }
      step={
        name === "OVERSIGHT_MAX_AGENTS" ||
        name === "OVERSIGHT_MAX_STEPS" ||
        name === "OVERSIGHT_SPEND_BRAKE_PERCENT"
          ? "1"
          : "any"
      }
      maxLength={512}
      placeholder={
        kind === "secret"
          ? "Replace key"
          : name === "OVERSIGHT_MAX_AGENTS"
            ? "4 (default)"
            : name === "OVERSIGHT_MAX_STEPS"
              ? "30 (default)"
              : name === "OVERSIGHT_SPEND_BRAKE_PERCENT"
                ? "90 (default)"
                : "Not set"
      }
      onChange={(event) => {
        if (!readOnly) onEdit(event.target.value);
      }}
      className={fieldClass}
    />
  );
}
