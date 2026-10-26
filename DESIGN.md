---
name: Oversight
description: A web IDE where one person supervises coding agents on a live graph of a Python repository.
colors:
  ground: "#000000"
  shell: "#060606"
  panel: "#080808"
  field: "#0b0b0b"
  page: "#0c0c0c"
  card: "#0e0e0e"
  raised: "#121212"
  page-raised: "#131313"
  text: "#ededea"
  muted: "#a3a39e"
  faint: "#85857f"
  code-dim: "#7d7d78"
  module-label: "#d2d2cd"
  divider: "#1c1c1c"
  border: "#2a2a2a"
  page-border: "#262626"
  card-border: "#242424"
  node-border: "#2e2e2e"
  outline: "#3a3a3a"
  control-edge: "#62625e"
  node-hover: "#6b6e77"
  edge: "#6a6a65"
  edge-possible: "#5c5c58"
  lit-edge: "#d6d3cb"
  work-light: "#8c8c88"
  agent-a: "#ff5c1f"
  agent-a-text: "#ff7a45"
  agent-a-tint: "rgba(255, 92, 31, 0.14)"
  agent-a-node: "rgba(30, 12, 4, 0.95)"
  agent-a-node-new: "rgba(20, 10, 4, 0.95)"
  agent-b: "#4f7dff"
  agent-b-text: "#86a3ff"
  agent-b-tint: "rgba(79, 125, 255, 0.16)"
  agent-b-node: "rgba(6, 12, 30, 0.95)"
  agent-b-node-new: "rgba(4, 8, 20, 0.95)"
  on-agent: "#17110b"
  passed: "#6fd08a"
  failed: "#ff6464"
  failed-text: "#ff7b7b"
  added-tint: "rgba(111, 208, 138, 0.11)"
  removed-tint: "rgba(255, 100, 100, 0.12)"
  syntax-plain: "#d8d7d0"
  syntax-keyword: "#9fa8b6"
  syntax-string: "#a9bea2"
  syntax-number: "#cbbd9f"
  syntax-builtin: "#b6adc6"
  syntax-definition: "#f2f1ea"
  day-ground: "#f4f3ef"
  day-shell: "#e9e8e3"
  day-panel: "#eeede8"
  day-field: "#fbfaf7"
  day-page: "#f8f7f3"
  day-card: "#fcfbf8"
  day-raised: "#e4e2dc"
  day-page-raised: "#efeee9"
  day-text: "#1c1c1a"
  day-muted: "#56554f"
  day-faint: "#67665f"
  day-code-dim: "#67665f"
  day-module-label: "#2b2a27"
  day-divider: "#dddbd4"
  day-border: "#d2d0c9"
  day-page-border: "#dcdad3"
  day-card-border: "#dad8d1"
  day-node-border: "#cfcdc6"
  day-outline: "#b9b7b0"
  day-control-edge: "#8a8983"
  day-node-hover: "#8d8c86"
  day-edge: "#6e6d67"
  day-edge-possible: "#8a8983"
  day-lit-edge: "#3a3936"
  day-work-light: "#6b6a64"
  day-agent-a: "#b23c0a"
  day-agent-a-tint: "rgba(178, 60, 10, 0.12)"
  day-agent-a-node: "#f9e7dd"
  day-agent-a-node-new: "#fbefe8"
  day-agent-b: "#2a4fc9"
  day-agent-b-tint: "rgba(42, 79, 201, 0.12)"
  day-agent-b-node: "#e6ebfa"
  day-agent-b-node-new: "#eff2fc"
  day-on-agent: "#ffffff"
  day-passed: "#1a6f36"
  day-failed: "#b8262b"
  day-added-tint: "rgba(26, 111, 54, 0.12)"
  day-removed-tint: "rgba(184, 38, 43, 0.10)"
  day-syntax-plain: "#2b2a27"
  day-syntax-keyword: "#4c5768"
  day-syntax-string: "#4a6644"
  day-syntax-number: "#77602f"
  day-syntax-builtin: "#64577a"
  day-syntax-definition: "#1c1c1a"
typography:
  headline:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.3
  brand:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "17px"
    fontWeight: 700
  title:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "16px"
    fontWeight: 700
  body:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "14px"
    fontWeight: 600
  caption:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  badge:
    fontFamily: "Atkinson Hyperlegible Next, sans-serif"
    fontSize: "13px"
    fontWeight: 700
  code:
    fontFamily: "Atkinson Hyperlegible Mono, monospace"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: "21px"
  data:
    fontFamily: "Atkinson Hyperlegible Mono, monospace"
    fontSize: "14px"
    fontWeight: 400
rounded:
  label: "4px"
  node: "5px"
  control: "7px"
  module: "8px"
  card: "10px"
  full: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "18px"
  overlay: "24px"
components:
  button-primary:
    backgroundColor: "{colors.text}"
    textColor: "{colors.page}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "0 16px"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "0 16px"
  button-destructive:
    backgroundColor: "transparent"
    textColor: "{colors.failed-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "0 16px"
  button-small:
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "34px"
    padding: "0 12px"
  input:
    backgroundColor: "{colors.field}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.card}"
    padding: "10px 12px"
  chip:
    backgroundColor: "{colors.page-raised}"
    textColor: "{colors.text}"
    typography: "{typography.caption}"
    rounded: "{rounded.control}"
    height: "30px"
  agent-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.card}"
  status-pill:
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "3px 10px"
  queue-counter:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "34px"
  graph-node:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.node}"
  module-frame:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.module-label}"
    rounded: "{rounded.module}"
  distance-badge:
    backgroundColor: "{colors.text}"
    textColor: "{colors.page}"
    rounded: "{rounded.full}"
    size: "20px"
  letter-avatar:
    backgroundColor: "{colors.agent-a-tint}"
    textColor: "{colors.agent-a}"
    rounded: "{rounded.card}"
    size: "36px"
---

# Design System: Oversight

## Overview

**Creative North Star: "Signal"**

Oversight has two surfaces with different jobs, and they never borrow each other's materials. The
graph is the machines' surface: it shows what the agents are doing, with nodes that light up, a
change in its agent's color, and, after level 1, rings and a field of dots that glow like an LED
panel. It is the only place where light moves. The panels are the person's surface: the agents
panel, review, live diff and file view are dark, flat and still. They hold text, decisions and code.

The product is a tool someone operates for long stretches while agents work, and the demo is
recorded on video. So the screen is dense but calm, every color means something, and nothing moves
or opens unless the person asked for it. Everything is dark by default, the review panel included,
with a dark Monaco theme. A day mode exists for the submission gallery only (see Colors).

**Precedence.** AGENTS.md wins over this file. The direction was agreed on the final Signal sketches
with the matte review header, made before the kickoff; this file takes values and rules from them,
never code or images. Where a sketch disagrees with AGENTS.md or with the quality floor below, this
file departs from it and says so in the table of departures. Sizes are CSS pixels on a 1920 by 1080
frame, the size the demo is recorded at. Panels scale with the window; type sizes and radii do not.

**Key Characteristics:**

- Black ground, warm white text, two agent colors and two state colors, nothing else.
- One family, Atkinson Hyperlegible, in sentence case.
- Flat panels with hairline borders; light lives only on the graph.
- Every state is told in words or signs as well as color.
- The layout never moves between modes; only the camera and the lights change.

### Departures from the sketches

| What | Sketch | Here | Why |
| --- | --- | --- | --- |
| Border of a changed node | solid | dashed | AGENTS.md: a dashed border means a pending change, changed and new alike |
| Module names on the graph | Mono | Next | AGENTS.md: graph labels use Next |
| Read nodes in review | lit in the agent's color | dimmed unless in the cone | the agent's color stays on the change only (Components, impact cone) |
| Code-dim (line numbers, comments) | `#6d6d68`, 3.8:1 on page | `#7d7d78`, 4.7:1 | quality floor: text at 4.5:1 |
| Text-box placeholder | `#6d6d68` | faint `#85857f`, 5.3:1 on field | same |
| Exact call edge | `#4a4a46`, 2.4:1 on black | `#6a6a65`, 3.7:1 | quality floor: meaningful graphics at 3:1 |
| Possible call edge | `#3a3a37`, 1.8:1 | `#5c5c58`, 3.0:1 | same; it stays thinner and grey |
| Input border | `#222222` to `#262626` | control-edge `#62625e`, 3.2:1 on field | quality floor: a control's boundary at 3:1 |
| Task box fill | `#070707` | field `#0b0b0b` | one fill for every input |
| Pressed rail button | `#161616` | raised `#121212` | one color for hover and pressed |
| New-node fill for agent B | not drawn | `rgba(4, 8, 20, 0.95)` | derived from A's pair, so both agents follow one rule |
| Live diff hint | "Pause to edit by hand" | read-only; editing happens in review | AGENTS.md: the new side is editable only in review while the agent is idle; pause is level 3 |
| Product name | "Graph IDE" | Oversight | the placeholder was replaced |

## Colors

A black field with warm near-whites, where orange and blue only ever mean Agent A and Agent B, and
green and red only ever mean passed and failed.

### Primary

- **Agent A orange** (agent-a): Agent A's node borders, letter badges, rings and light. As text it
  uses agent-a-text, lighter so small text stays readable on near-black.
- **Agent B blue** (agent-b): the same roles for Agent B, with agent-b-text for text. Orange and
  blue are the pair that people with the common forms of color blindness tell apart best.
- **Agent tints** (agent-a-tint, agent-b-tint): the fill behind a letter avatar or an agent's status
  pill. **Agent node fills** (agent-a-node, agent-b-node, and the slightly darker `-new` pair): the
  fill of a node with that agent's pending change.

### Secondary

- **Passed green** (passed): passing tests, added lines, the `+` count. Its tint (added-tint) is the
  background of an added line.
- **Failed red** (failed): failing tests, removed lines, the `-` count, Reject, Stop and discard.
  As text it uses failed-text. Its tint (removed-tint) is the background of a removed line.

### Neutral

From darkest to lightest:

- **Ground** (ground): behind the graph, and the page itself.
- **Shell** (shell): the app frame: top bar, left rail; graph overlays (legend, zoom) at 90%.
- **Panel** (panel): the agents panel; the cone card on the graph at 94%; module frames at 90%.
- **Field** (field): every input: search, task box, comment box.
- **Page** (page): the left panel: review, live diff, file view; Monaco's background.
- **Card** (card): an agent card; a graph node at rest, at 94%.
- **Raised** (raised): hover on a control or row; a pressed rail button; the queue counter.
- **Page raised** (page-raised): chips, facts and file rows inside the left panel.
- **Text** (text): primary text everywhere; a slightly warm white, because pure white glares on
  video against black.
- **Muted** (muted): secondary text: model name, times, counts, hints.
- **Faint** (faint): tertiary text: a module's function count, text-box placeholders.
- **Code-dim** (code-dim): line numbers and code comments, on page and field only.
- **Module label** (module-label): a module's name on the graph.
- **Borders:** divider between regions (under the top bar, beside the rail and panels); border for
  controls; page-border for chips, facts, file rows and text boxes in the left panel; card-border
  for an agent card and a module frame; node-border for a node at rest; outline for a secondary
  button; control-edge for the boundary of an input. All borders are 1px unless a graph state says
  otherwise.
- **Graph neutrals:** edge for an exact call edge, edge-possible for a possible one, lit-edge for an
  edge inside the cone or the change, node-hover for a hovered node's border, work-light for the
  light the cone casts.

### Code colors

Syntax colors are low in chroma, so code never looks like an agent or a state. Monaco starts from
its built-in dark theme (`vs-dark`) with these overrides: plain code syntax-plain, keywords
syntax-keyword, strings syntax-string, numbers syntax-number, built-ins syntax-builtin, a
function's name at its definition syntax-definition, comments and line numbers code-dim. A
highlighted span (the selected function) is white at 4%; a folded region is white at 3.5%.

### Day mode

Day mode exists for the submission gallery, which shows both modes. **The video and the demo are
dark.** Building day mode is a bounded front for Mon Nov 2, started only once level 2 is closed;
otherwise it is cut like level 3. The screenshots (`make shots`, once it exists) cover both modes.

- **Every color comes from a token**, including the colors Cytoscape, Monaco and the dot-field
  canvas receive. The token sets hang off `data-theme` on the root element (`dark` or `day`); no
  component picks a color itself, and switching the attribute repaints everything.
- **The light set** is the frontmatter's `day-` tokens: each `day-x` replaces `x`, and
  `day-agent-a` and `day-agent-b` also replace the agents' text variants. Panels are light warm
  greys of the same warm family as the dark set's text. White at 4% and 3.5% (span, fold) become
  black at 4% and 3%.
- **Monaco** uses its built-in light theme (`vs`) with the muted day syntax tokens.
- **The graph grid** is ink on paper: the ground is day-ground, unlit lamps are the ground darkened
  by about 4%, the guide dots are day-text at 10%, and light becomes ink density: a stronger light
  draws denser, darker dots in the light's day color.
- **Agents** are darker variants of the same hues, still told apart with common color blindness.
  Simulated color difference between A and B (CIE76, full-severity simulation): 119 with normal
  vision, 108 protanopia, 122 deuteranopia, 97 tritanopia (the dark pair: 137, 119, 140, 116). On a
  filled badge the letter is white.

Contrast of the day set (WCAG ratio; text needs 4.5:1, graphics and controls 3:1):

| Foreground | On | Ratio |
| --- | --- | --- |
| day-text | day-page / day-shell | 15.9 / 13.9 |
| day-muted | day-page / day-raised (worst) | 7.0 / 5.8 |
| day-faint, day-code-dim | day-page / day-shell (worst) | 5.4 / 4.7 |
| day-agent-a as text | day-card / day-page / day-shell (worst) | 5.7 / 5.5 / 4.8 |
| day-agent-a as text | its node fill day-agent-a-node | 4.9 |
| day-agent-b as text | day-card / day-page / day-shell (worst) | 6.0 / 6.4 / 5.6 |
| day-agent-b as text | its node fill day-agent-b-node | 5.8 |
| day-agent-a, day-agent-b as graphics | day-ground | 5.3 / 6.2 |
| white letter | day-agent-a / day-agent-b | 5.9 / 6.9 |
| day-passed | day-page / day-shell | 5.8 / 5.1 |
| day-failed | day-page / day-shell | 5.9 / 5.1 |
| day-edge / day-edge-possible | day-ground | 4.7 / 3.2 |
| day-control-edge | day-field / day-page | 3.4 / 3.3 |
| day-lit-edge / day-work-light | day-ground | 10.4 / 4.9 |
| day syntax: plain, keyword, string, number, built-in | day-page | 13.4, 6.8, 6.0, 5.6, 6.2 |

### Named Rules

**The Agent or State Rule.** A color is an agent (orange A, blue B) or a state (green passed or
added, red failed, removed or destructive). Nothing else gets a hue: no brand color, no accent, no
decorative gradient. Counters across agents are neutral.

**The Never Alone Rule.** Every agent color comes with its letter; every state color comes with a
word, a sign (`+`, `-`) or an icon. No state depends on color alone.

**The No Amber Rule.** There are no yellow or amber warnings. A warning is said in words with an
icon, in neutral colors.

## Typography

**Body Font:** Atkinson Hyperlegible Next (with sans-serif)
**Label/Mono Font:** Atkinson Hyperlegible Mono (with monospace)

**Character:** One family built for legibility: Next for text and graph labels, Mono for code,
hashes, money, step and line counts, and any number shown as data. Both come from Fontsource
packages; Monaco is created after the fonts load, or calls `monaco.editor.remeasureFonts()`.

### Hierarchy

- **Headline** (700, 20px, 1.3): the change's title at the top of the left panel.
- **Brand** (700, 17px): the product name in the top bar.
- **Title** (700, 16px): the agent's name on its card, section headings.
- **Body** (400, 15px, 1.5): running text, buttons, node labels. Prose such as the agent's note
  stays within about 75 characters a line.
- **Label** (600, 14px): buttons, pills, chips and labels that name something.
- **Caption** (400, 13px): captions, keyboard hints, a module's function count.
- **Badge** (700, 13px): the letter on a node badge; cursor names use 12px.
- **Code** (Mono 400, 15px, 21px lines): code in Monaco; line numbers at 13px.
- **Data** (Mono 400, 14px): hashes, money, counts shown as data.

The fonts need weights 400, 600 and 700 loaded.

### Named Rules

**The Sentence Case Rule.** Sentence case everywhere. No small uppercase labels, no letter-spaced
eyebrows.

**The Next on the Graph Rule.** Graph labels, module names included, use Next. A file path shown as
code inside a panel stays Mono.

## Layout

One screen, two modes, no tabs. The graph is always visible; a side panel changes with what the
person is doing.

- **Frame:** a top bar (52px) across the width; a left rail (56px) with the view icons.
- **Watch mode:** the graph fills the center; the agents panel (400px) sits on the right.
- **Review mode:** a left panel opens at about half the width (880px of 1920); the same graph stays
  on the right; the agents panel folds into the agents rail (56px).
- **Live diff and file view** use the same left panel. There is no third screen.
- **Graph overlays** sit 24px from the graph's edges: heading top left, cone card top left in
  review, legend bottom left, zoom controls bottom right.
- **Sizes:** buttons 40px tall, small buttons 34px; search box and queue counter 34px; chips 30px;
  letter avatar 36px; status lamp 10px; distance badge 20px.
- **Rhythm:** gaps of 6, 8, 10, 14 and 18px; tight inside a group, wider between groups.
- **Stable layout:** the demo repository has a saved preset layout, so the graph looks the same in
  every take. Switching modes changes the camera, never node positions. Orthogonal ("taxi") edges
  are tried on Tue Oct 27 and kept if they read better than curves.

## Elevation & Depth

Flat. There are no shadows on the person's surface; depth is tonal: ground, shell, panel, field,
page, card and raised step up in small increments of lightness, and hairline borders separate them.
Graph overlays sit on their surface color at 90 to 94% opacity, so the graph shows faintly through.
The one texture on the person's surface is the matte review header's still grain. The only light is
on the graph: node highlights, the dot field and the rings.

### Named Rules

**The Flat Panel Rule.** No blur, glass, glow or shadow on panels. If a panel needs separation, it
gets a divider.

## Shapes

Gently rounded rectangles throughout; nothing pill-shaped except status pills, badges and lamps.

- Card, file row, banner, text box: card radius (10px).
- Button, input, chip, fact: control radius (7px).
- Module frame: module radius (8px). Node: node radius (5px).
- Cursor name label: label radius (4px); keyboard hint: 5px.
- Status pill, distance badge, status lamp: fully round.
- The dash, 6px with a 4px gap, belongs to pending changes only.

