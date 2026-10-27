# Possible additions

Candidates beyond the three scope levels in AGENTS.md. They follow one idea, scalable oversight:
when agents produce more than a person can read, the tool has to show what needs the person's
attention. None of them starts before level 2 is done, and the cut rules still apply. The
maintainer picks which ones move into a level, and the most useful ones go in first, as time
allows. An idea that touches `contracts/` needs the maintainer's approval first (see the last
section).

## Proposed order

| # | Idea | What the person sees | Cost | Builds on |
| --- | --- | --- | --- | --- |
| 1 | Cross-review with Muse | One line per caller in the cone: how it depends on the change, which edge test is missing | small to medium | impact cone, agent B |
| 2 | Work zone, warn mode | Edits outside the zone the person drew, live on the graph and apart in review | small to medium | "start an agent from a function" (level 3) |
| 3 | Risk and "needs you" | A score per change with its reasons, and a line: "would approve alone" or "needs you" | small | cone, tests, zone |
| 4 | Verified cone | Each node of the cone checked by the tests that reach it: passed, failed or no tests | medium | run_tests, the "no tests" badge |
| 5 | Decisions as data | Rejected and approved versions with the comment, exported as preference pairs | small | Request changes |
| 6 | Rules on areas | Rules tied to nodes or modules, loaded by an agent when it enters that area | small to medium | Request changes, cross-review |

Smaller ones that fit beside these: "B reads compute_price, which A is changing" (a cheap form of
the cone overlap alert, level 3), and "said versus did": `finish` declares the files it touched and
the tests it ran, and the server compares that with the events ("says it ran the tests; it did
not").

## 1. Cross-review with Muse

When agent A proposes a change, agent B on Muse Spark Open, or a separate Muse Spark call, reads
every caller in the cone and writes one line per caller: how it depends on the change and which
edge test is missing (for example, exactly 10 and 50 units). The lines are marked as written by a
model. It brings the development rule "the author's model never reviews its own change" into the
product, and it puts both Meta models to work beyond the agent loop.

## 2. Work zone

Choosing a zone up front cannot know every edit the task will need, and a hard wall makes an
agent work around it: copied logic inside the zone, or a patch somewhere else. That is hidden
drift, worse than the edit it was meant to stop. So:

- **Reading is never limited.** The zone limits only what the agent edits.
- **The agent asks to leave.** A `request_scope` tool asks for a node outside the zone, with a
  reason ("compute_price needs a new argument for inventory"). The node shows as requested, in the
  agent's color, and the person approves or refuses it on the graph.
- **An edit outside without asking is marked.** Live on the graph in warn mode, and as "outside
  the zone" lines in review. In the demo, B leaves inventory.py, it shows at once, and Request
  changes follows.
- **Hard fences only where the person sets them**, such as a public API or a frozen module.
- **The zone grows with approvals**: an approved node stays inside it for the rest of the task.

## 3. Risk and "needs you"

Each change carries a score and its reasons: cone size, tests, edits outside the zone, cost. A
policy marks "would approve alone" or "needs you"; during the hackathon it never approves by
itself. It is one line in the review header.

**Test shortcuts: facts from the diff, the alarm from the run.** A detector that reads only the
diff cannot tell a weakened test from an intended behavior change. Measured on this repository's
own history, the diff-only detector (branch `review/shortcut-detector`) gave 101 findings in 90
commits, 16% of them worth a reviewer's time; a precision round brought it to 24 findings, about
half of them useful. Most test changes are legitimate and real shortcuts are rare, so the diff
alone has a ceiling. The product knows what a diff does not: the task, every `run_tests` result
with its failed test ids, and the order of the agent's edits. So the work splits in two:

- **Facts in review, never an alarm:** the detector's output as a neutral line in the review
  header: "tests in this change: 1 removed, 2 checks changed, 1 expected value changed".
- **"Needs you" from the run:** a test that failed in one of the agent's `run_tests` calls, whose
  checks the agent then removed, loosened, skipped or deleted, and which no longer fails in a
  later run. Optionally, only for those, one Muse Spark call with the task, the test diff and the
  results writes the reason ("loosened the assertion so it passes; the task did not ask for it").

The detector stays a pure function over plain data. The rule takes the change set and a small run
trace (each `run_tests` result's failed test ids and the edited paths, in order), built by an
adapter in the change manager from `AgentStep` events, so it needs no contract change for its
inputs and is tested without the runtime. The task bench (front B below) measures it with tasks
built to tempt a shortcut, such as a test that cannot pass without touching it.

## 4. Verified cone

Like test impact analysis: run only the tests that reach the cone's nodes, in the agent's
worktree, and mark each node passed, failed or without tests, so "possibly affected" becomes
"checked" where a test covers it.

## 5. Decisions as data

Every Request changes leaves a natural pair: the rejected version, the revised one that was
approved, and the comment. Exporting them as JSONL preference pairs makes them usable to evaluate
or tune an open model such as Muse Spark Open. During the hackathon, only the export.

## 6. Rules on areas

A rule tied to an area also saves context: the agent receives it only when it works there.

- **Not on the graph by default**, since every mark on the graph has one meaning. A "Rules" lens in
  the top bar lights the nodes and modules that have rules with the neutral work light, dims the
  rest, and shows a count on each module frame ("2 rules"). No color: color is for agents and
  states.
- **The detail lives in the side panel.** A selected node lists "Rules here": its own, the ones
  inherited from its class or module, and where each came from ("from Request changes on
  compute_price"). A list by area highlights a rule's scope on the graph when clicked.
- **Loading shows on the agent**, as a step on its card: "Loaded 2 rules for pricing".
- **Review lists only the rules that apply**; cross-review checks them, and a violation feeds
  "needs you".
- **Created** from Request changes ("Keep as a rule", scope suggested from the change's nodes) or
  by hand from a node. A module rule applies to all its nodes; repository-wide rules already live
  in AGENTS.md. Node ids come from qualified names, so a rule survives edits; a deleted or renamed
  node leaves the rule orphaned in the list, never lost silently. The agent receives rules as its
  own message when it enters the area, never inside a tool result.

## Isolated fronts outside the product

Fronts that never touch the product's code or contracts. Each lives in its own folder, worktree and
branch, and is reviewed like any other front. Unlike the ideas above, A and B may start now.

| # | Front | Why | Starts | Lives in |
| --- | --- | --- | --- | --- |
| A | Automated takes | The demo is 25%: identical, repeatable 1080p takes to edit, and a compression check | now | `tools/` and one recorded log |
| B | Task bench with numbers | Measured data for the pitch: tasks solved per agent, steps, cost, Muse Spark versus Muse Spark Open, later the shortcuts flagged | now, without the detector numbers | `tools/eval/` |
| C | TypeScript adapter | A second language for the graph, the most recognizable after Python | not before level 2 closes | its adapter folder, `fixtures/`, its tests |

**A. Automated takes.** A Playwright script drives replay mode and records video at 1080p along a
fixed path (clicks, zoom, review). It reuses the e2e replay setup. The takes come from a log
recorded from a real run, since only those may be labeled as a recorded run. The same takes serve
the compression check: encode at a low bitrate, or upload unlisted and watch at 720p.

**B. Task bench.** 15 to 20 scripted tasks on the demo repository, run through Oversight itself
(`POST /agents`), never by calling Meta's API directly, with results in a fixed format. Agents run
in Contributor mode. A cap in USD of API and Baseten credits is set before the first run. The line
"the detector flagged X shortcuts in N tasks" waits for the shortcut detector; the task set, the
script and the result format do not.

**C. TypeScript adapter.** tree-sitter (MIT) for TypeScript: modules, classes, functions and
methods with the same node kinds and stable ids as Python. Calls through explicit imports resolve
as "exact", the rest as "possible". It needs first graph core stage 2 in main and a small task that
puts the Python parser behind a language-adapter interface with no contract change; the new
dependency needs the maintainer's OK. A separate orchestrator runs it: Muse Code writes, Codex
reviews each PR, with a weekly cap on Muse Code usage, and it reports only when done or blocked.
Off by default, no merge before level 2 closes. The bar before it is shown: at least 70% of the
test repository's calls resolved as "exact", and the cone checked by hand in three cases. If it
passes, 5 seconds in the video and one line in the README; if not, it stays future work.

## Later, for the README's Next section

- **Behavior diff:** run the tests on the base and on the change, recording calls to the changed
  function, and show `compute_price(10): 90.0 -> 85.0`. The most useful for a reviewer, and the
  most costly.
- **Dependency guard:** when an agent adds a package, a node shows its license, age, known
  vulnerabilities (OSV) and whether it exists on PyPI, which catches hallucinated packages.
- **Bring your own agent:** other coding agents report their edits through hooks or MCP and are
  supervised on the same graph. After the hackathon, since during the event the product runs only
  Meta models.
- **Team integrations:** a pull request with evidence (cone, tests, a link to the replay), tasks
  from an issue tracker with their acceptance criteria, production errors on the graph, owners
  warned when the cone crosses their module.
- **Share a run as a link:** any run exported as a static replay.
- **Zoom levels** (already in the README).

## Contract impact, for the maintainer

Ideas 1 to 3 and 6 need contract additions: the agent's zone, the scope request as an event and a
command, an edit-outside-the-zone event, `finish` with declared fields, a risk score, and rules as
an entity with create, change and delete events and commands to read and write them. Proposed:
reserve them now as optional fields and as endpoints that answer 501, like the level 3 commands,
so a later level adds behavior without a breaking change. Nothing here is reserved until the
maintainer approves it.
