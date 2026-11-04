import type { EditableSettingName, SettingsView, UpdateSettings } from "@contracts";
import { createStore } from "zustand/vanilla";
import { commandFailure, createCommandClient } from "../../transport";

export type SettingsSection = "models" | "limits";
export const sectionFor = (name: EditableSettingName): SettingsSection =>
  name.endsWith("API_KEY") || name === "OVERSIGHT_CROSS_REVIEW" ? "models" : "limits";

type Drafts = Partial<Record<EditableSettingName, string>>;
interface SettingsApi {
  getSettings: () => Promise<SettingsView>;
  updateSettings: (request: UpdateSettings) => Promise<SettingsView>;
}
interface Editor {
  view: SettingsView | null;
  drafts: Drafts;
  loading: boolean;
  pending: SettingsSection | null;
  saved: Partial<Record<SettingsSection, boolean>>;
  failure: string | null;
  load: () => Promise<void>;
  edit: (name: EditableSettingName, value: string) => void;
  save: (section: SettingsSection) => Promise<void>;
}

export function createSettingsEditor(api: SettingsApi = createCommandClient()) {
  return createStore<Editor>()((set, get) => ({
    view: null,
    drafts: {},
    loading: false,
    pending: null,
    saved: {},
    failure: null,
    load: async () => {
      if (get().loading || get().view) return;
      set({ loading: true, failure: null });
      try {
        set({ view: await api.getSettings(), loading: false });
      } catch (error) {
        set({ loading: false, failure: commandFailure("Loading settings", error) });
      }
    },
    edit: (name, value) => {
      const { view, drafts, pending, saved } = get();
      const row = view?.settings.find((entry) => entry.name === name);
      if (!row || row.overridden || pending) return;
      const next: Drafts = Object.fromEntries(
        Object.entries(drafts).filter(([key]) => key !== name),
      );
      if (row.kind === "secret" || value !== (row.value ?? "")) next[name] = value;
      set({ drafts: next, saved: { ...saved, [sectionFor(name)]: false } });
    },
    save: async (section) => {
      const { drafts, pending, view } = get();
      if (pending || !view) return;
      const values: UpdateSettings["values"] = {};
      for (const row of view.settings) {
        const draft = drafts[row.name];
        if (draft !== undefined && !row.overridden && sectionFor(row.name) === section)
          values[row.name] = draft;
      }
      if (Object.keys(values).length === 0) return;
      set({ pending: section, failure: null });
      try {
        const result = await api.updateSettings({ values });
        const remaining: Drafts = Object.fromEntries(
          Object.entries(get().drafts).filter(
            ([name]) => sectionFor(name as EditableSettingName) !== section,
          ),
        );
        set({
          view: result,
          drafts: remaining,
          pending: null,
          saved: { ...get().saved, [section]: true },
        });
      } catch (error) {
        set({ pending: null, failure: commandFailure("Saving settings", error) });
      }
    },
  }));
}
