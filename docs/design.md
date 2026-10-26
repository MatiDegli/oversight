# Oversight design

This is the reference for how Oversight looks. It turns the visual direction the maintainer agreed
before the kickoff, "Signal" with a matte review header, into rules and values the frontend can
build from. AGENTS.md ("UI" and "Look") states the principles; this document gives the detail.

**Precedence.** AGENTS.md wins over this document. Where the agreed sketches disagree with
AGENTS.md, this document follows AGENTS.md and says so in a "Rule from AGENTS.md" note. The
sketches were made before the kickoff and only lent their values: no sketch code or image belongs
in this repository.

Sizes are CSS pixels on a 1920 by 1080 frame, the size the demo is recorded at. Panels scale with
the window; type sizes and radii do not.

## 1. Two surfaces

The screen has two surfaces with different jobs, and they never borrow each other's materials.

- **The graph is the machines' surface.** It shows what the agents are doing: nodes light up, a
  change carries its agent's color, rings spread from the node being edited. Later it gets a field
  of dots that light up like an LED panel (section 8). It is the only place where light moves.
- **The panels are the person's surface.** Agents panel, review panel, live diff and file view are
  dark, flat and still. They hold text, decisions and code. No glow, no gradient, no shadow, with
  one exception: the review header's matte grain (section 6.2), which never moves.

Everything is dark, the review panel included. Monaco uses a dark theme.

## 2. Tokens

The theme in `frontend/src/theme` owns these values. Components take colors only from the theme,
never as literals; Cytoscape and Monaco read the same values through the theme's TypeScript
exports.

### 2.1 Surfaces

From darkest to lightest:

| Token | Value | Where |
| --- | --- | --- |
| ground | `#000000` | behind the graph, and the page itself |
| shell | `#060606` | the app frame: top bar, left rail |
| panel | `#080808` | the agents panel |
| field | `#0b0b0b` | inputs (search, task box, comment box), graph overlays (legend, zoom) |
| page | `#0c0c0c` | the left panel: review, live diff, file view |
| card | `#0e0e0e` | an agent card |
| raised | `#121212` | hover on a control or a row; the pressed rail button |
| page-raised | `#131313` | chips and facts inside the left panel |

Graph overlays (legend, zoom controls, the cone card) sit on the field color at about 90% opacity,
so the graph shows faintly through them.

### 2.2 Text

| Token | Value | Use |
| --- | --- | --- |
| text | `#ededea` | primary text on every surface |
| muted | `#a3a39e` | secondary text: model name, times, counts, hints |
| faint | `#85857f` | tertiary text: placeholders, a module's function count |
| code-dim | `#6d6d68` | line numbers, comments, placeholder text in text boxes |

The text color is a slightly warm white, not pure white: on a black ground, pure white glares on
video.

### 2.3 Borders

| Token | Value | Use |
| --- | --- | --- |
| divider | `#1c1c1c` | lines between regions: under the top bar, beside the rail and the panels |
| border | `#2a2a2a` | controls and inputs |
| page-border | `#262626` | chips, facts, file rows and text boxes inside the left panel |
| card-border | `#242424` | an agent card; a module frame on the graph |
| node-border | `#2e2e2e` | a function, method or class node at rest |
| outline | `#3a3a3a` | a secondary button (outline style) |

All borders are 1 px unless a graph state below says otherwise.

### 2.4 Agent colors

Each agent is a letter and a color. Orange and blue are the pair that people with the common forms
of color blindness tell apart best.

| Token | A | B | Use |
| --- | --- | --- | --- |
| agent | `#ff5c1f` | `#4f7dff` | node borders, letter badges, rings, the agent's light |
| agent-text | `#ff7a45` | `#86a3ff` | the agent's color as text on a dark surface |
| agent-tint | `#ff5c1f` at 14% | `#4f7dff` at 16% | the fill behind a letter avatar or an agent status pill |

The base agent colors are fixed by AGENTS.md. The text variants are lighter so small text in the
agent's color stays readable on near-black.

### 2.5 State colors

| Token | Value | Use |
| --- | --- | --- |
| passed, added | `#6fd08a` | passing tests, added lines, the `+` count |
| failed, removed, destructive | `#ff6464` | failing tests, removed lines, the `-` count, Reject, Stop and discard |
| failed-text | `#ff7b7b` | red as text on a dark surface |
| added-tint | `#6fd08a` at 11% | background of an added line in a diff |
| removed-tint | `#ff6464` at 12% | background of a removed line in a diff |

### 2.6 Neutral light and dimming

| Token | Value | Use |
| --- | --- | --- |
| work-light | `#8c8c88` | the light the impact cone casts (section 3.5) |
| lit-edge | `#d6d3cb` | an edge inside the cone or the change, 2 px wide |
| dim-node | 16% opacity | nodes and edges outside the cone, in review and live diff |
| dim-module | 60% opacity | module frames, in review and live diff |

### 2.7 Code colors

Syntax colors are low in chroma, so code never looks like an agent or a state.

| Role | Value |
| --- | --- |
| plain code | `#d8d7d0` |
| keywords | `#9fa8b6` |
| strings | `#a9bea2` |
| numbers | `#cbbd9f` |
| built-ins | `#b6adc6` |
| function names at their definition | `#f2f1ea` |
| comments, line numbers | `#6d6d68` |
| a highlighted span (the selected function) | white at 4% |
| a folded region ("6 hidden lines") | white at 3.5% |

### 2.8 Type

- **Families.** Atkinson Hyperlegible Next for text and graph labels; Atkinson Hyperlegible Mono
  for code, hashes, money, counts of steps and lines, and any number shown as data. Both come from
  Fontsource packages.
- **Case.** Sentence case everywhere. No small uppercase labels.
- **Weights.** 400 for running text, 600 for buttons, pills and labels that name something, 700 for
  headings and the agent's name on its card.
- **Sizes.**

| Use | Size |
| --- | --- |
| panel heading (the change's title) | 20 px |
| brand | 17 px |
| agent name, section heading | 16 px |
| body, buttons, node labels, code | 15 px; code lines are 21 px tall |
| secondary text, pills, chips, small buttons | 14 px |
| captions, line numbers, keyboard hints | 13 px |
| letter badges on nodes, cursor names | 12 to 13 px |

**Rule from AGENTS.md.** The sketches set module names (`pricing.py`) in Mono. AGENTS.md puts graph
labels in Next, so module names on the graph use Next. A file path shown as code inside a panel
stays Mono.

### 2.9 Radii

| Element | Radius |
| --- | --- |
| card, file row, banner, text box | 10 px |
| control: button, input, chip, fact | 7 px |
| module frame | 8 px |
| node | 5 px |
| keyboard hint, cursor name label | 4 to 5 px |
| status pill, distance badge, status lamp | fully round |

### 2.10 Sizes

| Element | Size |
| --- | --- |
| top bar | 52 px tall |
| left rail | 56 px wide |
| agents panel (watch mode) | 400 px wide |
| agents rail (folded, review mode) | 56 px wide |
| left panel (review, live diff, file view) | 880 px on a 1920 px frame, about half the width |
| button | 40 px tall; small button 34 px |
| search box, queue counter | 34 px tall |
| chip | 30 px tall |
| letter avatar in the folded rail | 36 px square |
| status lamp | 10 px |
| distance badge | 20 px |

### 2.11 Motion

- Mode changes, dimming and highlights ease over 240 ms (`cubic-bezier(0.2, 0.7, 0.2, 1)`). The
  graph never moves its nodes between modes; only the camera and the lights change.
- A running agent's status lamp pulses with a 1.6 s period.
- Nothing on the person's surface animates on its own. The rings and the dot field (section 8) are
  the only continuous motion, and both live on the graph.

## 3. Graph states

A node's look answers two separate questions, each with its own visual channel:

1. **Is there a change here that is not in main yet?** The border style: dashed in the agent's
   color, or not dashed.
2. **What is the agent doing with it now?** The letter badge and the fill: an outlined letter for
   read, a filled letter and a tinted fill for an edit.

Each state is also said in words on the node ("read", "editing", "changed", "new"), so no state
depends on color alone.

### 3.1 Rest

- Node: fill `#0e0e0e` at 94% opacity, 1 px border `node-border`, radius 5 px, label in `text` at
  15 px.
- Module frame: fill `#080808` at 90% opacity, 1 px border `card-border`, radius 8 px; the module's
  name in `text`, its function count in `faint`.
- Call edge: 1.4 px, `#4a4a46`, with a small filled arrowhead of the same color.
- Hover: the node's border lightens to `#6b6e77`.

### 3.2 Read

The agent read the node in its current run.

- Border: solid, 1.5 px, in the agent's color.
- Letter badge: outlined (section 4).
- The word "read" in the agent's text color, beside the badge.
- Fill unchanged.

### 3.3 Edit

The agent is editing the node now (its last `agent.focus` on it was in edit mode, and the agent is
working).

- Border: dashed in the agent's color, 1.5 px (the change exists in its worktree: section 3.4).
- Fill: a dark tint of the agent's color: `rgba(30, 12, 4, 0.95)` for A, `rgba(6, 12, 30, 0.95)`
  for B.
- Letter badge: filled (section 4).
- The word "editing" in the agent's text color.
- After level 1, rings spread from this node (section 8.3).

### 3.4 Pending

The node carries a change from an agent that is not in main yet: the agent edited it and the
change is not approved, rejected or discarded.

- Border: **dashed**, 1.5 px, in the agent's color. The dash is 6 px with a 4 px gap.
- Fill: the agent's dark tint, as in edit. A node the agent added uses a slightly darker tint
  (`rgba(20, 10, 4, 0.95)` for A) than one it changed.
- Letter badge: filled.
- The word "changed" for an existing node, "new" for one the agent added.
- An edge the change adds is dashed in the agent's color, 2 px, with an arrowhead in that color.

**Rule from AGENTS.md.** The sketches drew a changed node with a solid border and kept the dash for
added nodes and edges. AGENTS.md says a dashed border means a pending change, in the agent's
color, so every node with an unapproved change is dashed, changed and new alike, and so is a node
being edited.

### 3.5 Impact cone

In review and in the live diff, the graph shows the change and its impact cone; everything else
steps back.

- The changed nodes keep their agent color (sections 3.3 and 3.4). The agent's color stays on the
  change and nowhere else.
- Nodes in the cone keep full opacity and show a distance badge (section 4.2) with their distance
  from the change: 1 for a direct caller, 2 for a caller of a caller, and so on.
- After level 1, the cone casts the neutral work light (`#8c8c88`), stronger for distance 1 than
  for farther nodes (section 8.2). Before that, the cone is told by opacity, badges and edges.
- Edges inside the cone and the change draw in `lit-edge`, 2 px, with a matching arrowhead.
- Everything else dims: nodes and edges to 16% opacity, module frames to 60%.
- The cone is "possibly affected", never a complete list. The cone card on the graph says what the
  bounds cut, for example "+12 omitted by depth".

### 3.6 Possible call edges

A "possible" call edge is thinner and grey: 0.9 px, `#3a3a37`, never dashed. It is drawn from
level 3; until then possible edges are not drawn.

### 3.7 Selected node

The node whose diff or file the left panel shows gets a 2 px ring in `text` around it.

## 4. Letter badges

### 4.1 Agent letters

Agents are a letter and a color, nothing more: no avatars, beads or mascots.

- **On a node.** A small rounded square at the node's right end with the letter A or B, 13 px,
  weight 700. For edit and pending, the square is filled with the agent's color and the letter is
  near-black (`#17110b`). For read, the square has a 1.5 px outline in the agent's color, no fill,
  and the letter in the agent's color.
- **In the folded agents rail.** A 36 px square with a 10 px radius: agent tint fill, 1.5 px border
  in the agent's color, the letter in the agent's color at 15 px, weight 700. A status lamp sits on
  its top-right corner, and a one-word caption under it ("Ready", "Editing").
- **On the agent card and in panel headings.** A small square swatch in the agent's color followed
  by the words "Agent A" or "Agent B".

### 4.2 Distance badges

A 20 px circle filled with `text`, the distance as a near-black number, 12 px, weight 700. They are
neutral: the cone is about the code, not about an agent.

## 5. Watch mode

The default mode: the graph fills the center, the agents panel sits on the right.

- **Top bar.** Product name, repository, branch and short commit hash (Mono), the search box, the
  review queue counter, the spend total (level 2), history and settings icons.
- **Review queue counter.** "1 change ready for review" with a lamp. It counts across agents, so it
  is neutral: `text` on `raised`, never an agent's color.
- **Graph overlays.** A heading with the repository name and counts ("7 modules, 19 functions, 2
  agents working"), a legend at the bottom left (Agent A, Agent B, read, editing or changed, not in
  main yet, possible call), zoom controls at the bottom right.
- **Agents panel.** A heading ("Agents", "2 of 2 slots", New task), one card per agent, the
  "Earlier today" history (level 3) and the task box at the bottom.
- **Agent card.** On `card` with a `card-border` border and a 10 px radius. First row: status lamp,
  agent name, model, and the status pill on the right. Then the task in one line, what the agent is
  doing now ("Editing restock in inventory.py"), a step progress bar with "Step 7 of 25" and the
  cost, and the last steps from `agent.step`, each with an icon, a short summary and its age.
  Buttons at the bottom: Live diff, and from level 2 Stop and discard.
- **Status pill.** The agent's state in words, on the agent's tint, with the agent's text color:
  "Editing", "Revising", "Ready for review". The pill belongs to one agent, so it may carry that
  agent's color.
- **Ready for review.** When an agent proposes its change, nothing on the screen moves or opens.
  The card's pill changes to "Ready for review", its border turns the agent's color, a "Review
  change" button appears on it, and the queue counter goes up.

## 6. Review mode

Opened only by the person: from "Review change" on a card, the queue counter, or a pending node.

### 6.1 Layout

- The left panel opens at about half the width (880 px of 1920), on `page`, with a `divider` on
  its edge.
- The same graph stays on the right, with the impact cone lit and the rest dimmed (section 3.5).
  The cone card at its top left lists the possibly affected functions by distance, with counts,
  and says what the bounds cut.
- The agents panel folds into the 56 px agents rail (section 4.1).

### 6.2 The matte header

The top of the review panel holds the change's title, the agent, its status pill, a close button,
and a row of facts: tests, lines added and removed, base commit, worktree, steps and cost.

Its background is matte: flat dark with a still grain.

- A gentle vertical gradient from about `#151515` at the top to about `#0f0f0f` at the bottom.
- A per-pixel grain of roughly plus or minus 2% brightness, fixed: it never animates. The grain
  also hides banding in the gradient.
- Agreed on the compressed demo clip: at 720p and 1.2 Mbps the grain disappears and the header
  stays clean. The rejected alternative, a dark pearl fabric, turned to grey smoke at that bitrate.
- It comes after level 1. Until then, and as the fallback, the header is plain `page`.

### 6.3 Body

- **Agent's note.** The agent's summary of the change, under the facts.
- **Diff.** Monaco's DiffEditor in inline mode, one block per file, with the file name, its `+`
  and `-` counts, and an inline or side-by-side switch. Added lines on `added-tint` with a `+`;
  removed lines on `removed-tint` with a `-`. Unchanged regions fold into a "6 hidden lines" row.
  Further files collapse to a row with their name and counts.
- **Editable new side (level 2).** A hint on the diff bar says the new side is editable and that
  edits go to the agent's worktree. It is editable only while the agent is idle.
- **Cone summary.** One line: how many functions are possibly affected. The full list lives on
  the graph's cone card.

### 6.4 Decisions

At the bottom: a comment box, then the three decisions.

- **Approve and merge.** The primary button: `text` fill with near-black text. It has no keyboard
  shortcut, not even Ctrl+Enter, because it merges into main and must never fire while the person
  types a comment. A note beside it says the tests rerun first if main moved.
- **Request changes** (level 2). An outline button. A line under the buttons says it keeps the
  agent's worktree and sends the comment back to the agent.
- **Reject.** Text and icon in `destructive`, no fill.

## 7. Live diff and file view

Both use the same left panel as review. There is no third screen.

- **Live diff.** Opened by clicking a node an agent is editing, or Live diff on its card. The
  header shows the task, the agent and its "Editing" pill, and the same facts row as review. A
  banner says the diff updates with each edit and that the decisions open when the agent proposes
  the change. The diff is read-only, with a lock and a line saying so on the diff bar. Below it,
  the agent's last steps. From level 2, Stop and discard in `destructive` text at the bottom. The
  graph shows the cone of the edit so far, with the same neutral light and dimming as review.
- **File view.** Opened by clicking a node with no pending change. It shows the file in main with
  the function's lines marked as a highlighted span. From level 2 it is editable, and edits go to
  main. On the graph, the selected node and its direct callers and callees stay lit in the neutral
  light; the rest dims.

## 8. Dot field and rings (after level 1)

The dot field and the rings come after level 1 and never block a level. Until they ship, and
whenever WebGL is missing, the graph's background is plain black.

### 8.1 The grid

- Its own canvas under a transparent Cytoscape canvas. It reads the rendered boxes of the lit
  nodes and draws their light; it never draws nodes or edges.
- A grid of 7 px cells, fixed to the screen: it does not pan or zoom with the graph. Each cell is a
  square lamp covering 72% of the cell (about 5 px), leaving a dark gap.
- Each lamp shows one of four levels (off and three steps of brightness), chosen with an 8 by 8
  ordered (Bayer) dither, so the light reads as a soft gradient made of discrete dots.
- An unlit lamp is not pure black: it sits at about 2% brightness (close to `shell`). A faint guide
  dot, 2 px, every 28 px, marks the grid where nothing is lit.
- The cells are large on purpose: they survive video compression at 720p.

### 8.2 Light

- Each lit node is a light source shaped like the node's box. Its intensity falls off with the
  distance from that box: a near term that decays quickly plus a far term that spreads wider
  (roughly 72% near and 28% far), times a gain for the mode and the node's weight.
- Where lights overlap, their colors mix by their intensity. At most 8 lights at once.
- The spread per mode (near falloff, far falloff, gain):

| Mode | Near | Far | Gain | Why |
| --- | --- | --- | --- | --- |
| Watch and live diff | 34 px | 90 px | 0.92 | the agents are the subject; their light spreads |
| File view | 22 px | 50 px | 0.80 | one function and its neighbors |
| Review | 16 px | 34 px | 0.70 | the cone carries the meaning, so the light is held in |

- What lights up, and with what weight:

| Mode | Lit nodes |
| --- | --- |
| Watch | per agent, in its color: the node being edited (or, once the agent is done, its main changed node) 1.0, other changed nodes 0.72, read nodes 0.36 |
| Review | the change in the agent's color: changed nodes 1.0, added nodes 0.8, read nodes 0.34; the cone in the work light: distance 1 at 0.52, distance 2 and beyond at 0.38 |
| Live diff | the node being edited 1.0 and read nodes 0.4, in the agent's color; the cone so far in the work light at 0.48 |
| File view | the selected node 0.8 and its direct callers and callees 0.32, all in the work light |

- In watch and live diff, the light of the node being edited breathes between 86% and 100% of its
  weight, about once every three seconds. In review it holds still.

### 8.3 Rings

- In watch and live diff, rings spread from the node an agent is editing, in that agent's color:
  one ring at a time, growing at 55 px per second from the node's center to 340 px, then starting
  again.
- A ring is a soft band about 10 px wide, at 90% strength, fading out between 60 px and 340 px of
  radius.
- No rings in review or file view: there, the cone and the selection carry the meaning.

## 9. Never

- **No amber.** No yellow or amber warnings. A warning is said in words with an icon, in neutral
  colors. A fact like "no test calls it" (level 3) is neutral, with an icon.
- **No color alone.** Every agent color comes with its letter; every state color comes with a word,
  a sign (`+`, `-`) or an icon (tests).
- **No color outside agents and states.** No brand colors, no decorative gradients, no accent hue.
  Counters across agents are neutral. The background never takes an agent's color unless that
  agent is working there.
- **Never steal focus.** Nothing opens, moves or scrolls by itself. When an agent finishes, the
  screen does not change; only its card, its pill and the queue counter do.
- **Dashes mean one thing.** A dashed border or edge means a change that is not in main yet, in its
  agent's color. Nothing else is dashed: not possible calls, not selection, not hover, not empty
  states.
- **No decoration on the person's surface.** No blur, glass, glow or shadow on panels. The only
  texture is the matte header's still grain.
- **No moving nodes.** Switching modes changes the camera and the lights, never the layout.
- **No shortcut approves.**

## 10. By level

| Level | Design elements |
| --- | --- |
| 1 | tokens and type; graph rest, read and edit states with letter badges; watch mode with agent cards and the queue counter; the "Ready for review" pill; read-only live diff; review with Approve and Reject; plain black graph background and plain review header |
| 2 | agent B's color; pending nodes and edges on the graph; impact cone with distance badges, dimming and the cone card; Request changes; editable new side; editable file view; spend total; Stop and discard |
| 3 | possible call edges; "no test calls it" badges; agent cursors with names in the editor; "Earlier today" history; cone overlap alerts; replay timeline |
| After level 1, never blocking | the dot field with its light, the rings, the matte header's grain |
