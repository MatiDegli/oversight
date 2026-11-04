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
    settings: [{ ...key, overridden: true }],
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
  expect(html).toContain("Restart to apply: File &gt; Reload settings.");
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
  store.getState().edit("META_API_KEY", "");
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
