// The person's appearance preference: a client-side choice kept in this browser only.
// theme/index.css and getActiveTheme() read the root's data-theme; this module only sets it.

export const appearances = ["dark", "day", "system"] as const;
export type Appearance = (typeof appearances)[number];
export type ThemeName = "dark" | "day";

export const APPEARANCE_KEY = "oversight.appearance";
const lightQuery = "(prefers-color-scheme: light)";

type MediaQuery = Pick<MediaQueryList, "matches" | "addEventListener" | "removeEventListener">;

/** What applying a preference touches. Each part is optional, as in tests and server rendering. */
export interface AppearanceEnv {
  root?: { dataset: DOMStringMap };
  storage?: Pick<Storage, "getItem" | "setItem">;
  matchMedia?: (query: string) => MediaQuery;
}

/** The page's own root, storage and media queries, each skipped when it is absent or refused. */
export function browserEnv(): AppearanceEnv {
  if (typeof window === "undefined") return {};
  let storage: AppearanceEnv["storage"];
  try {
    // Reading localStorage throws when the browser blocks storage for the page.
    storage = window.localStorage;
  } catch {
    storage = undefined;
  }
  const media =
    typeof window.matchMedia === "function" ? window.matchMedia.bind(window) : undefined;
  return {
    root: typeof document === "undefined" ? undefined : document.documentElement,
    storage,
    matchMedia: media,
  };
}

function isAppearance(value: unknown): value is Appearance {
  return appearances.some((name) => name === value);
}

/** The stored preference; a missing, invalid or unreadable value reads as dark. */
export function readAppearance(storage = browserEnv().storage): Appearance {
  try {
    const value = storage?.getItem(APPEARANCE_KEY);
    return isAppearance(value) ? value : "dark";
  } catch {
    return "dark";
  }
}

/** The theme a preference shows now: System follows the computer, dark unless it asks for light. */
export function resolveTheme(
  preference: Appearance,
  matchMedia = browserEnv().matchMedia,
): ThemeName {
  if (preference !== "system") return preference;
  return matchMedia?.(lightQuery).matches ? "day" : "dark";
}

let stopFollowing: (() => void) | undefined;
let firstTheme: ThemeName | undefined;
const listeners = new Set<() => void>();

/**
 * Sets the root's data-theme from the preference, the stored one by default. While System is
 * selected, a change of the computer's setting applies again; any later call replaces that listener.
 */
export function applyAppearance(
  preference?: Appearance,
  env: AppearanceEnv = browserEnv(),
): ThemeName {
  const chosen = preference ?? readAppearance(env.storage);
  stopFollowing?.();
  stopFollowing = undefined;
  const apply = () => {
    const theme = resolveTheme(chosen, env.matchMedia);
    if (env.root) env.root.dataset.theme = theme;
    for (const listener of listeners) listener();
    return theme;
  };
  if (chosen === "system" && env.matchMedia) {
    const query = env.matchMedia(lightQuery);
    const onChange = () => {
      apply();
    };
    query.addEventListener("change", onChange);
    stopFollowing = () => {
      query.removeEventListener("change", onChange);
    };
  }
  const theme = apply();
  firstTheme ??= theme;
  return theme;
}

/** Stores the person's choice and applies it at once. A refused write still applies it. */
export function chooseAppearance(
  preference: Appearance,
  env: AppearanceEnv = browserEnv(),
): ThemeName {
  try {
    env.storage?.setItem(APPEARANCE_KEY, preference);
  } catch {
    // Storage full or blocked: the choice holds until the page reloads.
  }
  return applyAppearance(preference, env);
}

/** The theme the page first painted with: canvas and editor colors were read from it. */
export function themeAtLoad(): ThemeName | undefined {
  return firstTheme;
}

/** Called after every apply, including the ones System triggers. */
export function subscribeAppearance(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
