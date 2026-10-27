# Possible additions

Candidates beyond the three scope levels in AGENTS.md. They follow one idea, scalable oversight:
when agents produce more than a person can read, the tool has to show what needs the person's
attention. None of them starts before level 2 is done, and the cut rules still apply. The
maintainer picks which ones move into a level, and the most useful ones go in first, as time
allows. An idea that touches `contracts/` needs the maintainer's approval first (see the last
section).

## Level 3 plan (decided on 2026-10-27)

The maintainer placed the ideas below on Tue Oct 27. Level 3 wiring starts when level 2 closes. Pure
cores start now: they add only new private files, change nothing the level 2 demo uses, and run on
Muse Code and Codex. Level 2 fronts go first in the review queue. Where the notes further down
differ, such as the condition for idea 9, this section wins.

| # | Package | Ideas | Decision |
| --- | --- | --- | --- |
| 1 | Merge alone when safe, with its history and Revert | 7 | Yes |
| 2 | Cross-review by the other Meta model; its blocking or should-fix findings go back as Request changes by themselves, at most one round | 1 | Yes |
| 3 | Facts in review: said versus did, tests with ids, size, and the shortcut detector's facts line | 3, 13 | Yes |
| 4 | Fails before, passes after, for new tests | 15 | Yes |
| 5 | The run rule: a test failed, was loosened, then passed | 3 | Yes; in the video only if a real run shows it |
| 6 | One task, one plan, two agents with zones, and the task queue, built on the commands the lead agent would use | 8, 2, 19 | Yes, after 1 |
| 7 | A light lead agent: Muse Spark with the public commands as tools (start an agent with a zone, read facts and reviews, request changes, wait); it never merges | 9 | Yes, if it passes the Sunday gate; otherwise Next |
| 8 | Graph polish and the dot-field light | | Yes, already planned |
| 9 | Delivery: static replay, automated takes, README with this project's own numbers | front A | Yes |
| 10 | Task bench, ten tasks, including a probe of Muse Spark as coordinator | front B | Yes, light |
| 11 | The graph's sibling functions in Request changes | 16 | If time remains |
| 12 | Start an agent from a function | level 3 list | If time remains |
| 13 | Dictation with Muse Voice Transcribe | level 3 list | If time remains, last day |
| 14 | Messages, pause and resume; the replay timeline | level 3 list | If time remains |

**Out, for the README's Next section:** the TypeScript adapter (front C), the verified cone (4),
rules on areas (6), decisions as data (5), the plan gate in protected areas (17), the Tauri shell,
agent B on the local GPU, and agent cursors in the editor.

**Limits:**

- **Shortcut detector:** its neutral facts line and the run rule, yes; an alarm based only on the
  diff, no. Under merge by rules, a removed or loosened test makes the change wait: a false alarm
  costs one click. The branch gets one final review before it merges, since its precision round
  has not had one.
- **Revert:** only for merges made by rules, and only while it applies without conflict; otherwise
  the change goes to "needs you". AGENTS.md records it as a bounded exception to "undo is out of
  scope", not as a general undo.
- **Demo script:** the level 2 script stays as the fallback. A new script replaces it only after it
  runs three times in a row from a clean start; that decision is taken on Mon Nov 2.
- **Contracts:** one proposal for everything above, with every new field optional and every new
  endpoint answering 501 until built, out by Wed Oct 28 for approval before Thu Oct 29. After that
  approval it is implemented in parts without another one. It reserves the agent that started
  another, even if the lead agent never ships.
- **Lead agent gate, Sun Nov 1 at 22:00 ART:** packages 1 to 6 on main with make check green, and a
  probe of Muse Spark as coordinator that meets criteria written before it runs: in 5 runs, at
  least 4 valid plans whose zones do not overlap; in at least 4 of 5, a correct Request changes in
  reaction to a review; and a cost per run under a cap the maintainer sets. If it fails, the lead
  agent goes to Next; the automatic Request changes of package 2 already covers the part of it
  that saves the most.

**Order:**

- **Tue Oct 27 to Wed Oct 28:** finish level 2 and the graph polish. In parallel, the pure cores in
  isolated fronts: the rule evaluator, the run rule, the cross-review parser and the plan
  validator, all tested with the scripted fake model. The coordinator probe with the real API
  (cents). The single contracts proposal. A plan to split the change manager before wiring 1 and 6.
- **Thu Oct 29:** level 1 checkpoint and, if possible, level 2 closes.
- **Fri Oct 30 and Sat Oct 31:** wire 3, 4, 2 and 1, in that order, with "Ask me" as the default.
  The dot-field light. A real recorded run for the replay. The task bench.
- **Sun Nov 1:** level 2 checkpoint (the two-agent demo, three times in a row). Plan and zones
  (package 6). The lead agent gate at night.
- **Mon Nov 2:** the lead agent, if it passed. The queue and the history, day mode, and the final
  recorded runs.
- **Tue Nov 3, until 20:00 ART:** robustness, the new demo three times in a row, and the takes.
- **Wed Nov 4:** video, README and submission at 20:00 ART.

## Proposed order

| # | Idea | What the person sees | Cost | Builds on |
| --- | --- | --- | --- | --- |
| 1 | Cross-review with Muse | One line per caller in the cone: how it depends on the change, which edge test is missing | small to medium | impact cone, agent B |
| 2 | Work zone, warn mode | Edits outside the zone the person drew, live on the graph and apart in review | small to medium | "start an agent from a function" (level 3) |
| 3 | Risk and "needs you" | A score per change with its reasons, and a line: "would approve alone" or "needs you" | small | cone, tests, zone |
| 4 | Verified cone | Each node of the cone checked by the tests that reach it: passed, failed or no tests | medium | run_tests, the "no tests" badge |
| 5 | Decisions as data | Rejected and approved versions with the comment, exported as preference pairs | small | Request changes |
| 6 | Rules on areas | Rules tied to nodes or modules, loaded by an agent when it enters that area | small to medium | Request changes, cross-review |
| 7 | Merge alone when safe | A mode in which a change that passes every rule merges by itself, with its reasons in the history; the rest waits as "needs you" | small to medium | tests on the combined result; 1 to 3 when approved |
| 8 | One task, one plan, two agents | A large task split into two subtasks with their zones, drawn on the graph and started with one click | medium | 2, 7, two agent slots |
| 9 | Lead agent | An agent that starts agents and requests changes on their work, never merges | medium to large | 7, 8 |

Ideas 7 to 9 go at the top of level 3, in that order, ahead of the other level 3 items; 9 only if
7 and 8 are done before the freeze.

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
policy marks "would approve alone" or "needs you". It is one line in the review header. In the
default mode it never approves by itself; idea 7 is the mode in which it does.

**Test shortcuts: facts from the diff, the alarm from the run.** A detector that reads only the
diff cannot tell a weakened test from an intended behavior change. The measurement is in
`tools/shortcut_corpus_report.md` on the branch `review/shortcut-detector` (read it with
`git show review/shortcut-detector:tools/shortcut_corpus_report.md`). On this repository's own
history, pinned at `f377dd4` (174 non-merge commits, 90 with Python changes), the diff-only
detector gave 101 findings, all labeled by a reviewer: 16% of them (1 shortcut and 15 useful) were
worth a reviewer's time. A precision round brought it to 24 findings, with 70 of the 85 noise rows
gone and 14 of the 15 useful rows kept. Of those 24, 1 is labeled a shortcut, 10 useful and 9 only
noise, and 4 are new and still unlabeled. So 11 of the 20 labeled ones (55%) are signal, which the
report itself does not call a complete precision estimate for all 24. Most test changes are
legitimate and real shortcuts are rare (one labeled in 101), so the diff alone has a ceiling. The
product knows what a diff does not: the task, every `run_tests` result with its failed test ids,
and the order of the agent's edits. So the work splits in two:

- **Facts in review, never an alarm:** the detector's output as a neutral line in the review
  header: "tests in this change: 1 removed, 2 checks changed, 1 expected value changed".
- **"Needs you" from the run:** a test that failed in one of the agent's `run_tests` calls, whose
  checks the agent then removed, loosened, skipped or deleted, and which no longer fails in a
  later run. Optionally, only for those, one Muse Spark call with the task, the test diff and the
  results writes the reason ("loosened the assertion so it passes; the task did not ask for it").

Integrating the detector into the product is a level 3 decision. Closing level 2 does not
authorize it: it needs the maintainer's own decision, inside level 3.

The detector stays a pure function over plain data. The rule takes the change set and a small run
trace, built by an adapter in the change manager from events that already exist, so it needs no
contract change for its inputs and is tested without the runtime:

- **Failed test ids** come from `AgentStep.tests` on each `run_tests` step.
- **Edited paths, in order,** come from `agent.focus` in edit mode and from `change.updated`.
  `AgentStep` has no structured edited paths: its summary is text built from the tool arguments.
- **A truncated list is not "fixed".** `failed_tests` holds at most 20 ids. A test missing from a
  list shorter than the run's `failed` count is unknown, not "no longer fails". Only a run that
  finished with its whole list (`failed` equals the list length) can show that a test passes again.

The task bench (front B below) measures the rule with tasks built to tempt a shortcut, such as a
test that cannot pass without touching it.

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

## 7. Merge alone when safe

The person who runs several agents is held up by approving every change by hand, not by the
agents. This repository is built the other way: since `docs/approvals`, the coordinator merges a
front once the checks pass and a reviewer from another provider has no open blocking or should-fix
finding (for frontend views and docs, a should-fix off the demo path becomes a follow-up front;
see docs/proposal.md, section 7), and only the critical decisions reach the maintainer. The product
is stricter than the process that builds it. This idea brings that process into the product.

- **Two modes:** "Ask me", the default and today's behavior, and "Merge alone when safe". The
  level 1 and level 2 demos run in "Ask me".
- **A change merges alone only if every rule holds:** the tests pass on main as it is now; no test
  is removed, skipped or loosened (the detector's facts from idea 3, used here as a condition: a
  false alarm only makes the change wait, which is today's cost); every changed function is
  reached by a test in the call graph; every edit is inside its zone and none touches a protected
  area; the other model's review (idea 1) has nothing blocking; size, cone and spend are under
  their limits. A change to a function with a large cone, such as `compute_price`, always waits.
- **Anything else is "needs you"**, with the failed rule as the first line of the review header.
- **Ideas 1 to 3 are still not approved.** Approving ideas 7 to 9 does not approve them. The first
  version of 7 works without them: the rules above that come from them (the detector's facts from
  idea 3, the zone and the protected areas from idea 2, the other model's review from idea 1) are
  added only as each of those ideas is approved and lands. The maintainer will set the initial
  required rules in a separate plan. An unavailable required check means "needs you". For tests:
  until idea 3's detector lands, any change that edits or deletes an existing test file needs you;
  added tests are fine.
- **A model can call the person in; only code lets a change through without them.** A model's
  output can add a reason to wait, never remove one.
- **Every merge by rules is in the history with its reasons**, and has a one-click Revert while it
  applies cleanly. Undo is out of scope in AGENTS.md, so the Revert needs the maintainer's OK.
- **Merge as in the repository's integration flow:** test the combined result first, then move
  main.
- **The pitch line:** decide the rules once and the exceptions live. If the idea ships, the
  success sentence in PRODUCT.md ("nothing reaches main without their decision") would need to
  change. Proposed: "nothing reaches main without their decision or a rule they set, and they can
  see why each change merged". PRODUCT.md stays as it is until the maintainer approves this
  sentence on its own.

## 8. One task, one plan, two agents

The person writes one larger task. Muse Spark proposes two subtasks, each with its zone and its
text, and the plan is drawn on the graph in A's and B's colors before anything starts. One click
starts both; the person can edit or refuse the plan. Overlapping zones are flagged before the start,
which avoids most conflicts later. The server runs the plan, not a model: the model only proposes
the split, and idea 7 decides the merges. This mirrors the repository's process, where the
maintainer approves the proposal before any code and the coordinator integrates after, and it is
deterministic enough for a demo that runs three times in a row.

## 9. Lead agent

An agent with tools to start agents, read their changes and request changes on them. It never
merges and never approves: merges stay with idea 7 or the person. Only if 7 and 8 are done before
the freeze. It runs on Meta models like every agent in the product.

## Ideas 10 to 21: oversight as a property of the harness

Proposed on 2026-10-27 and not yet placed: the maintainer decides which ones move into the
hackathon and which stay out of scope.

In a multi-agent coding workflow built from command-line agents, scripts and a chat, the effort
goes to the same five places: the coordinating agent's context, which rereads the whole state on
every turn; claims in hand-backs that someone checks by hand; rules that only an agent remembers;
state written by hand in markdown; and the person, who carries messages between sessions and finds
overspending and drift late. The harness, the coordination and the person's surface are three
separate pieces, glued together by a model and by the person. Oversight is the three in one
process: its agents run inside it with fixed tools, and every step is an event. What such a
workflow keeps as a rule, Oversight can enforce as a property of the harness.

The principle behind all of them: the server holds the state, starts the agents, waits and merges.
A model is called without state for one judgment at a time (split a task, review a change, write a
handoff), on a small context the server builds. Idea 8 already works this way.

| # | Idea | What changes | Cost | Builds on |
| --- | --- | --- | --- | --- |
| 10 | Task with "done when" | The task form takes a goal, a "done when" line and the zone; the harness supplies the rest | small | 2, `POST /agents` |
| 11 | Budget check and reserve | The agent hears at half its step budget, and the last steps stay reserved for `finish` | small | the tool loop, `halted` |
| 12 | Context cap | The runtime owns the history and caps it; past the cap, the run continues from a generated handoff | small to medium | the tool loop |
| 13 | Generated hand-back | The change's report is built from events; the agent writes only what is still open | small | "said versus did" |
| 14 | Review tied to the version | A review or assessment belongs to one revision of the change; a new edit voids it | small | change revisions, 7 |
| 15 | Fails before, passes after | New tests also run on the base; one that passes there does not count as testing the change | small to medium | run_tests, 7 |
| 16 | Fresh fix round with siblings | Request changes can start a fresh run from a generated handoff, with the callers and siblings of each finding's node | medium | Request changes, the graph |
| 17 | Plan before code in protected areas | In a protected area, `edit_file` stays blocked until the person approves the agent's plan | medium | 2, 6 |
| 18 | Follow main by itself | When main moves, the server retests the change on the new main; a conflict goes back to the agent or to "needs you" | medium | tests on the combined result, 7 |
| 19 | Task queue | Waiting tasks start by themselves when an agent slot frees up | small | 8 |
| 20 | Cost and rounds per accepted change | Spend and review rounds per accepted change, per role and per model, and the pace against the budget | small | the spend meter |
| 21 | Decision cards | "Needs you" as a card with options and a recommended one, kept as data | small | 3, 5 |

Where they could go, as a proposal: 13, 14, 15 and 20 are cheap and add rules to idea 7; 19 goes
with idea 8; 16, 17 and focused tests by cone (idea 4) only if time remains after level 2. The rest
is the maintainer's call.

**10. Task with "done when".** The task is three fields: the goal, one "done when" line and the
zone. The base is main's HEAD by construction; the step budget, the checks, the report format and
what stays untouched come from the harness, not from the text. At start, the server checks the
task's premises against the graph: every node the zone names exists.

**11. Budget check and reserve.** At half the step budget, the loop adds one message with the steps
used and left. The last steps are kept for `finish`, so a run near its limit proposes what it has
instead of being cut off mid-edit. A run that stops already tries to propose its change as it is
(`halted`), while its worktree is still there.

**12. Context cap.** Every step resends the whole history, so cost grows with the length of the
run. The runtime owns that history: past a cap, it closes the run's context and starts a fresh one
from a generated handoff (the task, the files changed, the last test result), shown as a step on
the agent's card.

**13. Generated hand-back.** Oversight saw every edit and every test run, so the report is facts,
not claims: the files and nodes changed, the tests run with their ids and results, the steps, the
duration and the cost. The agent writes only what is still open. Where its `finish` disagrees with
the events, the review shows it ("said versus did").

**14. Review tied to the version.** A model's review, an assessment for idea 7 or the person's
comment names the revision and commit it saw. A later edit marks it stale, and a stale review never
counts toward a merge.

**15. Fails before, passes after.** The tests a change adds also run on its base. A new test that
already passes there does not prove the change: the review says so, and under idea 7 the change
needs you.

**16. Fresh fix round with siblings.** In level 2, Request changes sends the comment into the
agent's own history, as AGENTS.md says. The level 3 option: start a fresh run from a generated
handoff with the findings, the diff and the failing tests, instead of the whole earlier history.
The graph adds the callers and siblings of each finding's node, since a fix round often finds the
same problem one function away.

**17. Plan before code in protected areas.** In an area marked protected (ideas 2 and 6), the agent
may read, but `edit_file` fails until the person approves a short plan the agent writes. The gate
belongs to the tool, not to a rule the agent has to remember.

**18. Follow main by itself.** In "Merge alone when safe", a change whose base moved is retested on
the new main by the server, never by the agent. A conflict sends the change back to its agent with
the conflicting files, or to "needs you". In "Ask me", the person still chooses, as today.

**19. Task queue.** The person queues tasks; when an agent finishes or stops and its slot frees up,
the server starts the next one. Overlapping zones are flagged before the start, as in idea 8.

**20. Cost and rounds per accepted change.** Every model call goes through one gate, so cost can be
split by change, by role (writer or reviewer) and by model, live, together with the review rounds
per accepted change and the pace of spend against the budget. Overspending shows while it happens,
not after.

**21. Decision cards.** An escalation becomes a card in the "needs you" list: the question, two or
three options with the recommended one first, and the evidence. The answer is kept as data with the
change, next to idea 5's pairs.

**Already covered elsewhere:** a reviewer from the other Meta model with a clean context (idea 1:
it receives the task, the diff and the cone, never the author's reasoning); overlap by node, live
(the level 3 cone overlap alert, and idea 8); rules loaded only where they apply (idea 6); focused
tests by cone (idea 4); measured comparisons on the same task set (front B); outside agents on the
same graph (the README's Next section).

**Already true, for the pitch:** agents have no shell; each agent has its own worktree; the server
runs the tests with a timeout; the event log is the state, and replay is its audit; a halted run
tries to propose its change while its worktree is still there; every model call goes through one
gate; the spend brake refuses new Meta runs above 90% of the Meta credits. The gap is the same as in any such tool, and worth saying: tests run code with
the user's permissions, and a sandbox is out of scope.

## Isolated fronts outside the product

Fronts that never touch the product's code or contracts. Each lives in its own folder, worktree and
branch, and is reviewed like any other front. Unlike the ideas above, A and B may start now.

| # | Front | Why | Starts | Lives in |
| --- | --- | --- | --- | --- |
| A | Automated takes | The demo is 25%: identical, repeatable 1080p takes to edit, and a compression check | now | `tools/` and one recorded log |
| B | Task bench with numbers | Measured data for the pitch: tasks solved per agent, steps, cost, Muse Spark versus Muse Spark Open, later the shortcuts flagged | now, without the detector numbers | `tools/eval/` |
| C | TypeScript adapter | A second language for the graph, the most recognizable after Python | closed until level 2 closes | its adapter folder, `fixtures/`, its tests |

**A. Automated takes.** A Playwright script drives replay mode and records video at 1080p along a
fixed path (clicks, zoom, review). It reuses the e2e replay setup. The takes come from a log
recorded from a real run, since only those may be labeled as a recorded run. The same takes serve
the compression check: encode at a low bitrate, or upload unlisted and watch at 720p.

**B. Task bench.** 15 to 20 scripted tasks on the demo repository, run through Oversight itself
(`POST /agents`), never by calling Meta's API directly, with results in a fixed format. A few of
the tasks are built to tempt a shortcut. Agents run in Contributor mode. A cap in USD of API and
Baseten credits is proposed to the maintainer before the first paid run. The line "the detector
flagged X shortcuts in N tasks" waits for the shortcut detector; the task set, the script and the
result format do not.

**C. TypeScript adapter.** tree-sitter (MIT) for TypeScript: modules, classes, functions and
methods with the same node kinds and stable ids as Python. Calls through explicit imports resolve
as "exact", the rest as "possible". It stays closed until level 2 closes: no worktree, no branch and
no work before then, not only no merge. It is written as a standalone adapter that reads graph core
stage 2 from main without editing it, and it never touches the product's code, including the
parser. Plugging it into the product means putting the Python parser behind a language-adapter
interface, which edits the parser: that is a separate product front, outside C, and it needs the
maintainer's decision after level 2 closes. The new dependency also needs the maintainer's OK. A
separate orchestrator runs it: Muse Code writes, Codex reviews each PR, with a weekly cap on Muse
Code usage, and it reports only when done or blocked. Off by default. The bar before it is shown:
at least 70% of the test repository's calls resolved as "exact", and the cone checked by hand in
three cases. If it passes, 5 seconds in the video and one line in the README; if not, it stays
future work.

## Later, for the README's Next section

- **Behavior diff:** run the tests on the base and on the change, recording calls to the changed
  function, and show `compute_price(10): 90.0 -> 85.0`. The most useful for a reviewer, and the
  most costly.
- **Dependency guard:** when an agent adds a package, a node shows its license, age, known
  vulnerabilities (OSV) and whether it exists on PyPI, which catches hallucinated packages.
- **Bring your own agent:** other coding agents report their edits through hooks or MCP and are
  supervised on the same graph. It is the bridge to supervising the multi-agent development
  workflows that today run in terminals and a chat. After the hackathon, since during the event the
  product runs only Meta models.
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

Ideas 7 to 9 add: who decided a change (`decided_by`, the person or the rules) and the reasons on
`change.resolved`; the merge mode as a setting; a Revert command with its own graph delta cause;
a plan entity with its subtasks and zones, and the plan an agent belongs to; for idea 9, the agent
that started another one.

Ideas 10 to 21 would add: a "done when" field on the task; the revision a review saw (idea 7's
assessment already records it); the base result of added tests; a handoff and a round number on a
run; a plan-approval state for an agent in a protected area; a queued task; cost per change and per
role; and decision cards with their answers. None of them is reserved until the maintainer approves
it.
