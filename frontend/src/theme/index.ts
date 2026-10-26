// Keep these values in sync with index.css; the parity test covers every token.
// Cytoscape and Monaco consume these constants through the theme facade.
export const colors = {
  background: "#000000",
  panel: "#161616",
  text: "#eeeeee",
  muted: "#a3a3a3",
  border: "#404040",
} as const;

export const agentColors = {
  A: "#ff5c1f",
  B: "#4f7dff",
} as const;

export const stateColors = {
  passed: "#66bb88",
  added: "#66bb88",
  failed: "#ee7777",
  removed: "#ee7777",
  destructive: "#ee7777",
} as const;

export const fontFamilies = {
  sans: '"Atkinson Hyperlegible Next", sans-serif',
  mono: '"Atkinson Hyperlegible Mono", monospace',
} as const;
