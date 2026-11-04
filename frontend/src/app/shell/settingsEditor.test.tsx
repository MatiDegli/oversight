import type { EditableSetting, SettingsView } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { CommandError } from "../../transport";
import { EditableSettings } from "./EditableSettings";
import { createSettingsEditor } from "./settingsEditor";

const key: EditableSetting = {
  name: "META_API_KEY",
  kind: "secret",
  state: "set",
  value: null,
  overridden: false,
};
const view: SettingsView = {
  settings: [
    key,
    { name: "OVERSIGHT_BUDGET_USD", kind: "number", state: "set", value: "5", overridden: false },
  ],
  restart_required: false,
  apply: "server_restart",
};
function setup(result: SettingsView = view) {
  const api = {
    getSettings: vi.fn().mockResolvedValue(result),
    updateSettings: vi.fn().mockResolvedValue({ ...result, restart_required: true }),
  };
  const store = createSettingsEditor(api);
  return { api, store };
}
function markup(store: ReturnType<typeof createSettingsEditor>, live = true) {
  return renderToStaticMarkup(
    <EditableSettings live={live} store={{ ...store, getInitialState: store.getState }} />,
  );
}
it("loads secret fields empty and sends only changed fields from the saved section", async () => {
  const { api, store } = setup();
  await store.getState().load();
  const html = markup(store);
  expect(html).toContain('type="password"');
  expect(html).toContain('placeholder="Replace key"');
  expect(html).toContain('value=""');
  expect(html).toContain("Configured");
  expect(html).not.toContain("Saved.");
  store.getState().edit("META_API_KEY", "new-synthetic-key");
  store.getState().edit("OVERSIGHT_BUDGET_USD", "9");
  await store.getState().save("models");
  expect(api.updateSettings).toHaveBeenCalledExactlyOnceWith({
    values: { META_API_KEY: "new-synthetic-key" },
  });
  expect(store.getState().drafts).toEqual({ OVERSIGHT_BUDGET_USD: "9" });
  const saved = markup(store);
  expect(saved).toContain("Saved.");
  expect(saved).toContain("Restart the server to apply.");
  expect(saved).not.toContain("new-synthetic-key");
});
it("retains refused values and shows the server's message in an alert", async () => {
  const { api, store } = setup();
  await store.getState().load();
  api.updateSettings.mockRejectedValue(
    new CommandError(422, "invalid_request", "Invalid settings: META_API_KEY."),
  );
  store.getState().edit("META_API_KEY", "typed-synthetic-key");
  await store.getState().save("models");
  expect(store.getState().drafts.META_API_KEY).toBe("typed-synthetic-key");
  const html = markup(store);
  expect(html).toContain('role="alert"');
  expect(html).toContain("Invalid settings: META_API_KEY.");
  expect(html).not.toContain("Saved.");
});
it("keeps overridden rows read-only and labels desktop restart persistently", async () => {
  const { api, store } = setup({
    ...view,
    settings: [{ ...key, overridden: true }, ...view.settings.slice(1)],
    apply: "desktop_reload",
    restart_required: true,
  });
  await store.getState().load();
  store.getState().edit("META_API_KEY", "ignored");
  await store.getState().save("models");
  expect(api.updateSettings).not.toHaveBeenCalled();
  const html = markup(store);
  expect(html).toContain("Set by the environment");
  expect(html).toContain("readOnly");
  expect(html).not.toContain("Restart to apply:");
  store.getState().edit("OVERSIGHT_BUDGET_USD", "9");
  await store.getState().save("limits");
  expect(markup(store)).toContain("Restart to apply: File &gt; Reload settings.");
});
it("renders both sections read-only in replay without fetching settings", () => {
  const { api, store } = setup();
  const html = markup(store, false);
  expect(html).toContain("Models and keys");
  expect(html).toContain("Limits and budget");
  expect(html).toContain("Settings are read-only in a replay.");
  expect(html).toContain("readOnly");
  expect(api.getSettings).not.toHaveBeenCalled();
  expect(api.updateSettings).not.toHaveBeenCalled();
});
it("drops reverted numeric drafts and serializes saves without losing the other section", async () => {
  const { api, store } = setup();
  await store.getState().load();
  store.getState().edit("OVERSIGHT_BUDGET_USD", "9");
  store.getState().edit("OVERSIGHT_BUDGET_USD", "5");
  await store.getState().save("limits");
  expect(api.updateSettings).not.toHaveBeenCalled();
  store.getState().clearKey("META_API_KEY");
  await store.getState().save("models");
  expect(api.updateSettings).toHaveBeenCalledExactlyOnceWith({ values: { META_API_KEY: "" } });
});

it("labels the brake's budget share and explains both empty-field defaults", () => {
  const { store } = setup();
  const html = markup(store);
  expect(html).toContain("Brake at (% of budget)");
  expect(html).toContain("Leave empty to use 90%.");
  expect(html).toContain("Leave empty to use 4 agents.");
});

it("drops a deleted typed key, and only explicit Clear key queues an undoable removal", async () => {
  const { api, store } = setup({
    ...view,
    settings: [
      ...view.settings,
      {
        name: "OVERSIGHT_CROSS_REVIEW",
        kind: "boolean",
        state: "set",
        value: "false",
        overridden: false,
      },
    ],
  });
  await store.getState().load();
  store.getState().edit("META_API_KEY", "replacement");
  store.getState().edit("META_API_KEY", "");
  store.getState().edit("OVERSIGHT_CROSS_REVIEW", "true");
  await store.getState().save("models");
  expect(api.updateSettings).toHaveBeenCalledExactlyOnceWith({
    values: { OVERSIGHT_CROSS_REVIEW: "true" },
  });
  store.getState().clearKey("META_API_KEY");
  expect(markup(store)).toContain("Key will be cleared when you save");
  expect(markup(store)).toContain("Undo");
  store.getState().undo("META_API_KEY");
  expect(store.getState().drafts).toEqual({});
  expect(markup(store)).not.toContain("Key will be cleared when you save");
  store.getState().clearKey("META_API_KEY");
  await store.getState().save("models");
  expect(api.updateSettings).toHaveBeenLastCalledWith({ values: { META_API_KEY: "" } });
  expect(markup(store)).not.toContain("Key will be cleared when you save");
});

it.each(["models", "limits"] as const)(
  "keeps %s refusal and restart feedback beside its Save",
  async (section) => {
    const { api, store } = setup();
    await store.getState().load();
    const name = section === "models" ? "META_API_KEY" : "OVERSIGHT_BUDGET_USD";
    store.getState().edit(name, "9");
    api.updateSettings.mockRejectedValueOnce(
      new CommandError(422, "invalid_request", "Synthetic refusal."),
    );
    await store.getState().save(section);
    const sections = () =>
      [...markup(store).matchAll(/<section\b.*?<\/section>/gs)]
        .map((match) => match[0])
        .filter((html) => !html.includes(">Repository</h2>"));
    const index = section === "models" ? 0 : 1;
    expect(sections()[index]).toContain("Synthetic refusal.");
    expect(sections()[1 - index]).not.toContain("Synthetic refusal.");
    await store.getState().save(section);
    expect(sections()[index]).toContain("Saved.");
    expect(sections()[index]).toContain("Restart the server to apply.");
    expect(sections()[1 - index]).not.toContain("Restart the server to apply.");
  },
);

it.each(["models", "limits"] as const)(
  "keeps the restart reminder under the successful %s save after another section refuses",
  async (section) => {
    const { api, store } = setup();
    await store.getState().load();
    const other = section === "models" ? "limits" : "models";
    store.getState().edit("META_API_KEY", "replacement");
    store.getState().edit("OVERSIGHT_BUDGET_USD", "9");
    await store.getState().save(section);
    // Editing again removes Saved., but the persisted change still needs a restart.
    store.getState().edit(section === "models" ? "META_API_KEY" : "OVERSIGHT_BUDGET_USD", "10");
    api.updateSettings.mockRejectedValueOnce(
      new CommandError(422, "invalid_request", "Other section refused."),
    );
    await store.getState().save(other);
    const sections = [...markup(store).matchAll(/<section\b.*?<\/section>/gs)]
      .map((match) => match[0])
      .filter((html) => !html.includes(">Repository</h2>"));
    const index = section === "models" ? 0 : 1;
    expect(sections[index]).toContain("Restart the server to apply.");
    expect(sections[index]).not.toContain("Other section refused.");
    expect(sections[1 - index]).toContain("Other section refused.");
    expect(sections[1 - index]).not.toContain("Restart the server to apply.");
  },
);

it("does not attach a restart reminder to unsaved sections on load", async () => {
  const { store } = setup({ ...view, restart_required: true });
  await store.getState().load();
  expect(markup(store)).not.toContain("Restart the server to apply.");
});

it.each([
  "OVERSIGHT_BUDGET_USD",
  "OVERSIGHT_SPEND_BRAKE_PERCENT",
  "MUSE_SPARK_INPUT_USD_PER_M",
  "MUSE_SPARK_OUTPUT_USD_PER_M",
  "OVERSIGHT_BASETEN_BUDGET_USD",
  "BASETEN_INPUT_USD_PER_M",
  "BASETEN_OUTPUT_USD_PER_M",
] as const)("says saved %s applies to the next run in one status line", async (name) => {
  const result: SettingsView = {
    ...view,
    settings: [{ name, kind: "number", state: "set", value: "10", overridden: false }],
  };
  const { api, store } = setup(result);
  api.updateSettings.mockResolvedValue(result);
  await store.getState().load();
  store.getState().edit(name, "20");
  await store.getState().save("limits");
  const html = markup(store);
  expect(html).toContain(
    '<div role="status" class="text-muted"><p>Saved. Applies to the next run.</p></div>',
  );
  expect(html).not.toContain("Restart");
  expect(html).not.toContain("<p>Saved.</p>");
});

it("keeps restart feedback when live and restart-only fields were saved together", async () => {
  const { store } = setup({
    ...view,
    settings: [
      ...view.settings,
      { name: "OVERSIGHT_MAX_AGENTS", kind: "number", state: "set", value: "4", overridden: false },
    ],
  });
  await store.getState().load();
  store.getState().edit("OVERSIGHT_BUDGET_USD", "20");
  store.getState().edit("OVERSIGHT_MAX_AGENTS", "8");
  await store.getState().save("limits");
  expect(markup(store)).toContain("Restart the server to apply.");
  expect(markup(store)).not.toContain("Applies to the next run.");
});

it("shows plain Saved for a restart-only value restored to its startup value", async () => {
  const { api, store } = setup();
  api.updateSettings.mockResolvedValue(view);
  await store.getState().load();
  store.getState().edit("META_API_KEY", "restored-synthetic-key");
  await store.getState().save("models");
  expect(markup(store)).toContain("<p>Saved.</p>");
  expect(markup(store)).not.toContain("Applies to the next run.");
});

it("explains the optional Meta budget and both default token prices beside their fields", () => {
  const { store } = setup();
  const html = markup(store);
  for (const [name, helper] of [
    ["OVERSIGHT_BUDGET_USD", "Leave empty for no spending cap."],
    ["MUSE_SPARK_INPUT_USD_PER_M", "Leave empty to use 0.10"],
    ["MUSE_SPARK_OUTPUT_USD_PER_M", "Leave empty to use 0.20"],
  ] as const) {
    const input = new RegExp(`<input[^>]*name="${name}"[^>]*>`).exec(html)?.[0];
    const descriptionId = /aria-describedby="([^"]+)"/.exec(input ?? "")?.[1] ?? "";
    expect(descriptionId).not.toBe("");
    expect(html).toContain(`id="${descriptionId}"`);
    expect(html.slice(html.indexOf(`id="${descriptionId}"`))).toContain(helper);
  }
});
it.each([
  [
    "desktop_reload",
    "To review another repository, choose File &gt; Open repository… in the menu bar.",
  ],
  ["server_restart", "The repository is chosen when the server starts."],
] as const)(
  "says how to review another repository when settings apply by %s",
  async (apply, line) => {
    const { store } = setup({ ...view, apply });
    await store.getState().load();
    const html = renderToStaticMarkup(
      <EditableSettings
        live
        workspace="demo-repo"
        store={{ ...store, getInitialState: store.getState }}
      />,
    );
    expect(html).toContain(">Repository</h2>");
    expect(html).toContain("demo-repo</span>, never your folder.");
    expect(html).toContain(line);
    expect(html.indexOf(">Repository</h2>")).toBeLessThan(html.indexOf("Models and keys"));
  },
);
it("points a replay to the Windows app's folder picker without a desktop claim", () => {
  const { store } = setup();
  const html = markup(store, false);
  expect(html).toContain(
    "In the Windows app, choose File &gt; Open repository… to review your own repository.",
  );
  expect(html).toContain("Agents edit a disposable copy, never your folder.");
  expect(html).not.toContain("in the menu bar.");
});
