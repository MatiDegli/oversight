# Beyond level 2

## Three proofs

The freeze is organized around three proofs. Judge each front by the proof it serves.

1. **Would you approve it?** Package 9's static challenge is the submission link and opens the
   video. It must work at phone width so a judge can try it on a phone. The maintainer sees a
   phone-width shot before merge.
2. **One person, many agents.** Run for a few hours on a real open-source repository with a
   permissive license, credited in the video. The coordinator proposes the repository and tasks
   to the maintainer first and reports budget pace before starting. End with counts of changes
   merged by rules, questions asked and differences tests missed, plus cost per accepted change.
   Paid calls for this run have a US$25 Meta-budget cap, separate from the bench's US$20.
   End on the decisions inbox: this many agents for this many hours, and these were the only
   decisions the person had to make.
3. **Unrequested behavior among green patches.** From that same run, measure the share of patches
   with passing tests whose behavior diff reveals a difference the task did not request. Fix the
   reporting rule before measuring: if the share is low or the sample too small, tell the scale
   story without a percentage. Do not repeat the run to improve that result.

Checkpoint: Sat Oct 31 at 23:00 ART. Items 1, 2, 4 and 9, the behavior-diff bench and proof 1
must all be on main. If any is missing, cut work from item 12 down and tell the maintainer what
was cut.

## Decided queue

This is the working home for everything beyond level 2. The maintainer set this order on
2026-10-28. When capacity opens, take the first unfinished item that can proceed; dates do not
supersede priority. Level 2 closed on Oct 27; its demo remains the fallback. Queue numbers replace
the old idea numbers. Package numbers identify the Oct 27 work packages. Keep the item numbers:
work through 1–11, then the lead agent, saved flows, 12, 13, 17 and 18; take 14–16 only if time
remains. The two additions after item 11 are unnumbered.

States were checked against main at `535e6f6`, including its code and merge history. In-progress
branches are the coordinator's assigned fronts, not merged work. Queued rows describe what remains,
including where part is already on main. The descriptions give code evidence and boundaries.

**In contracts proposal:** **Yes** means included in the requested Oct 28 proposal, not approved
or already reserved; **No** means no addition is needed; **Pending** means a contract question
needs the maintainer's decision. Existing reservations are noted below. This document does not
amend [the approved proposal](proposal.md) or authorize edits to `contracts/`.

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 1 | Facts and a summary from events (package 3) | Queued — summary remains; facts on main `366d0e5`, facts row/steps `ae9822d` | Yes — summary; report check needs no field |
| 2 | Behavior diff | In progress — `changes/behavior-diff`, core behind a setting; absent on main `535e6f6` | Yes |
| 3 | Agent harness pack | In progress — `agents/harness-pack`; current tools on main `535e6f6` | No |
| 4 | Measured impact cone | Queued — caller recording absent at `535e6f6`; isolation on main `19443e8` | Yes |
| 5 | Live overlap between agents | Queued — card facts/read history remain; shared pending-node marks on main `535e6f6` | No |
| 6 | Fails before, passes after (package 4) | Queued — assertion versus import/setup error only; probes on main `b7122ef`, isolated in `19443e8` | No — conservative classification uses existing fields; exception type declined |
| 7 | Cross-review and automatic Request changes (package 2) | On main — `95e4723`, `9b81d0b`, `13f955c`, `7da6fa9`; no new front | No |
| 8 | Merge by rules, follow main and Revert (package 1) | In progress — `changes/rules-reservation` (P2b-2); scheduler on main `9f70622`, isolation `19443e8`; merge/Revert remain | Yes — protected_paths rule in a new policy profile version; merge/Revert shapes reserved |
| 9 | A change card with three states | Queued — full card absent at `535e6f6`; cross-review Needs you note on main `7da6fa9` | Yes |
| 10 | Cost and rounds per accepted change | Queued — aggregates remain; writer/reviewer attribution on main `13f955c` and `535e6f6` | Yes — stored aggregates; event-derived totals need none |
| 11 | One plan, server-computed zones and task queue (package 6) | Queued — main `535e6f6` has validator/reservations, no partitioner or dispatcher | Yes — subtask dependencies and a relaxed plan validator for sequential/single-agent starts |
| — | [Lead agent](#lead-agent) | Queued — after items 8 and 11 reach main; thin layer on item 11 | No — lead routes and `started_by` reserved |
| — | [Saved agent flows](#saved-agent-flows) | Queued — after the lead agent; extends package 12, on main `c1c19c6` | No while filling task text; a distinct role or tool limits need maintainer review |
| 12 | Examples before code | Queued — no examples phase on main `535e6f6` | Yes |
| 13 | Pinned examples and graph lenses | Queued — no pins or lens control on main `535e6f6` | Yes |
| 17 | Run rule (package 5) | Queued — wiring remains; pure core on main `aaa9af8`, facts producer absent at `535e6f6` | Yes — run-rule finding kind for removed, skipped, deleted checks |
| 18 | Remaining packages 11–14 | Queued — 13–14 remain; 11 on main `ee92691`, 12 on main `c1c19c6`; do not rebuild them | Yes — separate dictation endpoint, initially 501; message/pause/resume reserved; replay/input controls local |
| — | [Standing grants](#standing-grants) | Plan proposed 2026-10-29 in [standing-grants.md](plans/standing-grants.md); position awaits the maintainer | Separate 1.7.0 proposal (2026-10-29), not in the Oct 28 set — grant events, `ApproveChange.grant`, `ChangeResolved.grants`, revoke route |

### Only if time remains

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 14 | Node card explained by examples | Queued — observed call inputs/results absent at `535e6f6`; navigation is on main | Yes |
| 15 | Shape guard | Queued — no structural comparison producer on main `535e6f6` | Yes |
| 16 | Bounded one-hop context for initial tasks | Queued — broader slice waits for bench; revision callers/siblings on main `ee92691` | No |

**Not ranked, goes in regardless:** delivery (package 9; static replay on main `a0a4624`, challenge
queued after item 2), graph polish (8; graph and palette on main `535e6f6`, polish continues), and
the task bench (10; eight-task base on main `9a1772f`, arms in progress on `tools/bench-arms`).

## Undecided candidates

These retain useful ideas from the old inventory without approving a front or changing the freeze.

- Budget reminders with steps reserved for `finish`, and a bounded history with a visible handoff.
- Fresh revision context built from facts, distinct from the existing retained-history revision.
- No repeated automatic revision for an unchanged failure: before cross-review or rules follow-up
  starts a round, compare the test and assertion with the last failure and the agent's edits since
  then. A changed node reaches the test if the test can reach it through the graph's call edges,
  or it appears in that test's executed nodes when recorded. If nothing relevant changed, the card
  asks the person. This is deterministic, with no model call; if neither source can tell, ask
  instead of starting another round on a guess.
- Behavior diff for methods, only if level 3 is reached. Until then, measure the third proof on
  tasks that change functions only and state that limit in the write-up; current targets are
  module-level functions (`backend/src/oversight/changes/_behavior.py:79`).
- A dependency guard for package existence, license and vulnerability evidence.
- Export reviewed revisions/comments as decision pairs; no training during this event.
- A person-approved plan before editing protected areas; item 8's deterministic veto is separate.
- Person-drawn work zones that limit edits, with a scope-request tool, warn mode and zone growth by approval; item 11's server-computed zones and the reserved `within_zone` rule are separate.
- A TypeScript graph adapter; another language/dependency needs a separate scope decision.
- Outside agents reporting through hooks or MCP, after the hackathon's Meta-only product scope.
- Team integrations, evidence attached to pull requests, and exporting any run as a shareable replay.
- Zoom levels (see README), a Tauri shell, a local model in slot B, and named editor cursors.

## Next

- Hidden test sets: extend beyond the external hidden grading already present in `tools/eval`.
- A red agent to look for counterexamples to a proposed change.
- A specialist engine for checks that need a dedicated tool.
- Two independent attempts on one task: twice the generation cost; separate from the existing automatic revision round.
- Executable disagreement between the two models, resolved with an example.
- Canary changes: a product workflow beyond the seeded defects already in the task bench.
- Fork a run from a recorded step.
- Answer a review question from a phone.
- Text rules attached to areas of the repository.
- Tell the later agent about an overlap in its next tool result; each agent already has a worktree.
- Select tests from the graph inside the agent loop, starting in shadow mode.
- Per-test coverage, beyond the per-test outcomes already recorded.
- Mask older tool results on long runs; measure dollars and correctness before adoption.
- Generate inputs for behavior that the bounded probes did not reach.
- Run the bench on a real repository.

## No

- Mutation testing: focus on behavior comparisons rather than add another test-generation pipeline.
- Uncertainty from token logprobs: likelihood over code tokens is not evidence of a behavioral
  defect. Item 9's constrained Spark Open choice is separate: probability over a finite decision
  can only add a reason to wait, never approve a change.
- Test pending changes together before first approval: preserve one candidate's evidence and decision at a time.
- Voice approval: a merge needs an explicit review decision; dictation may enter task text only.
- Autonomy learned from approval history: permission comes from rules the person chose.
- 3D or city views: retain the single readable graph as the supervision surface.
- A learned risk score: expose deterministic reasons and missing evidence instead of an opaque score.
- An agent graph query language or slices of two or more hops: keep context bounded and the graph primarily for the person.
- Free-form agent chat: dependencies and handoffs belong to the server's state machine.
- Agent-owned locks: the server owns scheduling and git locks.
- Prompts requesting several approaches: use one evidence-led path with a stopping point.
- Hypothesis: its MPL-2.0 license is outside the dependency allowlist.
- Trained verifiers: no training pipeline within this hackathon.
- Tree search: branching does not fit the bounded workflow and budget.

## Item descriptions

### 1. Facts and a summary from events

Give the person at most three lines covering the request, actual edits, tests added/run, other
results changed and cost. Derive them from task/status, change, execution and usage events, never
the author's `finish` prose. Missing paired results must say they were not compared.

Package 3 already publishes source facts and execution references. The shared facts row and recent
steps landed in `ae9822d` (`frontend/src/review/ChangeFactsRow.tsx:76`). Recognized report claims
such as “tests pass” already produce contradictions against the last agent run
(`frontend/src/review/ReviewFacts.tsx:66`, `backend/src/oversight/changes/_reviewfacts.py:209`).
Reuse these checks: they do not understand arbitrary prose and need no new field. A stored
generated summary belongs in the requested optional additions. Do not put report contradictions
into `ChangeFacts.integrity`, whose kinds describe test-source observations.

### 2. Behavior diff

Show a difference count and up to eight input/before/after rows per changed function on its node
and in review. Run base and candidate in separate fresh processes on identical bounded inputs:
literal test arguments, plus `c-1`, `c`, `c+1` for integer constants in either module, and `0`/`-1`
for integer arguments. Bind by parameter name; compare typed values and supported exceptions.
Execute each differing input twice in total on each side. Results varying between runs are
unstable, not an established change.

Build the core first behind a setting on `changes/behavior-diff`; publish only after its optional
per-node contract field, including unstable/not-probed outcomes, is approved. Reuse the demo
interpreter, asynchronous bounded runner, guarded base checkout and C3 session isolation
(`19443e8`; `backend/src/oversight/workspace/capability.py:116`). Never fall back to an open network.
Apply time/output/input caps across the whole probe, not a full suite allowance per input. Write
fresh implementation; exploratory prototypes supply no product code.

Report non-literal arguments, unsupported signatures, side effects, I/O and randomness as not
probed, with reasons. Network isolation is not filesystem isolation. This makes no model calls and
proves no equivalence. Empty output reads “no difference measured on these inputs”.

### 3. Agent harness pack

Reduce avoidable failures without changing the six tools. Under one setting: reject a Python edit
that breaks previously valid syntax, return the error line and preserve old bytes; summarize broad
searches with per-file match counts and a narrowing hint; retain a bounded whole failure block
with test/assertion/relevant frame; remind `finish` once to run tests if edits followed the last
run; prompt for one evidence-led path and a stopping point.

`agents/harness-pack` builds on current hash/uniqueness checks and bounded observations
(`backend/src/oversight/agents/tools.py:388`, `backend/src/oversight/agents/_observations.py:13`).
Preserve failures before the runner discards text. No contract addition or tool is needed. One
setting lets the bench compare both arms with the same model, reasoning and limits. This does
not replace independent server proposal tests.

### 4. Measured impact cone

Distinguish callers with observed result changes from those only possibly affected. Extend item 2
through the external pytest reporting plugin: run the same tests on both implementations, record
inputs/results at caller boundaries, match by test/caller/order, and compare only equal inputs.
Tests construct the objects; do not serialize/rebuild arbitrary objects to invoke callers.

Main's plugin records phases/outcomes only
(`backend/src/oversight/workspace/report_plugin/oversight_report.py:26`). Add bounded recording
and proposed optional measured-cone evidence, including unmatched calls/incompleteness. Both runs
require session network isolation, never open-network execution. Unsupported or unmatched calls
are not compared; other nodes stay “possible, no change measured”. This is neither per-test
coverage nor proof that all callers behave identically.

### 5. Live overlap between agents

Identify same-node edits on the node and both cards. If B has read something A is changing, say so
on B's card and retain observed reads for the whole change. Edits inside another agent's cone
become a fact at review. Never interrupt the person or move focus for overlap.

Reuse `pending-both` (`frontend/src/graph/styles.ts:302`), pending-node sets and live/proposal cones.
Add pure selectors and change-lifetime read sets to the reducer; main retains only latest focus
(`frontend/src/state/applyEvent.ts:89`). No contract addition; replay must agree with live runs.
Search focus is capped, so observed reads are not a complete audit. Once item 18 supplies messages,
notify the later agent as a separate turn; notification in its next tool result remains Next.

### 6. Fails before, passes after

Tell the person whether a new test demonstrates the change or merely failed to import or set up
on base. Keep package 4's case discovery, selected base/head probes and review output (`b7122ef`);
C3 already requires isolated probes (`backend/src/oversight/changes/_probes.py:229`). Only evidence
classification remains.

Today a base `error` counts as failure (`backend/src/oversight/changes/_beforeafter.py:95`). Classify
import/setup errors as unknown with their diagnostic, using existing probe results/problems;
the merge-alone evaluator must never use them as evidence of failure on base. Build this before
merge-alone can activate, with no contract change. An exception-type field was declined for the
Oct 28 proposal. Do not rebuild the functioning probes or claim assertion evidence from an error.

### 7. Cross-review and automatic Request changes

An adverse independent review can send one revision back without the person relaying it. This is
on main through `7da6fa9`, following `95e4723`, `9b81d0b` and `13f955c`
(`backend/src/oversight/runtime_review.py:167`, `backend/src/oversight/changes/_cross_review.py:19`).
Drop it as a front. Reuse target validation, durable accounting, human preemption and the one-round
limit in item 8. It is opt-in. Since `045f589`, reviewer selection uses `OVERSIGHT_REVIEW_MODEL`,
independent of slots (`backend/src/oversight/review_config.py:26-38`), with `muse-spark-open` as the
default (`backend/src/oversight/settings.py:62-63`). The reviewer's model must differ from the
author's (`backend/src/oversight/review_config.py:37`); another call to the author's model is not
a fallback.
Both authors, A and B, write code with hosted Muse Spark. Muse Spark Open is the cross-reviewer
and item 9's deciding model; the cross-review mechanism stays unchanged.
The manual review route remains 501, which does not mean automatic review is missing. No new
contract is needed for this item.

### 8. Merge by rules, follow main and Revert

Ask me still waits on every change. Optional rules mode merges only with complete, current passing
evidence, recorded reasons and clean one-click Revert. The server retests a moved base; failures
or conflicts become Needs you. Models can add reasons to wait, never waive a deterministic rule.

Before enabling rules mode, `PRODUCT.md` needs a maintainer-approved success sentence covering the person's decision or a rule they set as the authority for every merge.

Main has policy profiles, the closed mode controller and P2b-1 scheduler (`9f70622`). Admission
explicitly waits for reservation (`backend/src/oversight/changes/_assessment.py:39`); P2b-2 is in
progress on `changes/rules-reservation`. Finish reservation, merge transaction/final race barriers,
Revert recovery/history, routes and UI before enabling the mode. C3 landed in `19443e8`; rules
and probes require it. Human approval already retests moved bases
(`backend/src/oversight/changes/_merge.py:45`), and revision can merge main into the retained branch.
Reuse those paths; rules must test the combined result even when base has not moved.
Enable cross-review with the other model before merge-alone can activate; the merge-alone evaluator
must consume its result and require no blocking finding, alongside the deterministic checks.

Manifest/lockfile, CI, test-configuration and security-path edits always wait for the person and
receive cross-review. The maintainer included a named `protected_paths` rule in the Oct 28 contract
proposal, with a new policy profile version: `RuleId` is closed and profiles validate against it
(`contracts/src/oversight_contracts/common.py:44`). A server-only veto would hide the policy reason.
This approves its inclusion in the proposal, not a change to the current contracts. Revert and
resolution provenance are already reserved. Revert covers only rules merges that apply cleanly;
conflicts wait for the person. This is not general undo; unknown required evidence never passes.

### 9. A change card with three states

The card says merged by rules, Needs you for a choice, or Needs you because a check was unavailable.
A rules merge shows the summary, scope of results checked as unchanged, and Revert. For an
unrequested choice, put one plain question and its smallest before/after example ahead of unchanged
facts. Yes confirms the choice; No requests changes with the example as comment; Unsure waits.
Ask me keeps the summary on top and requires Approve for every change.

The decisions inbox is the form of Needs you within item 9. One place lists every decision
waiting for the person across all changes and agents: the card's Yes/No/Unsure questions;
checks that could not run; a failure or conflict while following main (item 8); a risky-area
change (item 8); the lead's plan when it needs approval; and proposed examples before code
(item 12). Each entry shows its change or agent, why it waits, its smallest example and its
age, oldest blocking first. A header count shows how many decisions are pending.

The person answers from the inbox. The answer goes to its originating change; No is Request
changes to its author, as today. If the person picks another agent, start a new task for that
agent with the answer as its text. Unsure stays in the inbox. Questions come only from Oversight,
never from the agent.

Derive the inbox as a pure function over existing events in the frontend reducer, like the
overlap in item 5, so it survives restarts and appears in replay, with no field beyond item 9's.
One contract question remains open: an answer routed to another agent may need an optional link
to come back as answered after a restart or in replay. Today a start has no reference back to a
card, and answers persist only in the change's resolution. The maintainer has not answered this.

Build the inbox together with item 9, keeping item numbers, anchors and the decided queue's order.
It changes the look, so the maintainer sees a shot before merge.

For each measured behavior difference, Muse Spark Open chooses asked for, not asked for or
cannot tell against the task text or confirmed examples. Constrain the output with a `json_schema`
enum or a grammar. Log probabilities over those options supply a decision probability only if
the probe and development examples support it. Below a threshold, or on cannot tell, the change
waits. This decision can add a reason to wait, never approve. Confirmed-example results remain
deterministic. Oversight asks selectively, never through the agent's report.

Probe first with `tools/probe_api.py`: check log probabilities and structured output on both
endpoints, with reasoning off for this call. Then use about ten development examples to check
whether the probability separates correct from incorrect answers. Build probability handling only
if both checks hold. Otherwise retain the constrained choice, with cannot tell as its only reason
to wait, and report the findings to the maintainer. Set the threshold from development examples,
never from the 20 frozen bench patches. Hosted Muse Spark writes code for both A and B and runs
the lead; Spark Open supplies cross-review and this decision. Cross-review stays as it is.

Reuse target-aware notes (`frontend/src/review/CrossReviewNote.tsx:11`); full projection is absent
and the reducer ignores assessments/Revert (`frontend/src/state/applyEvent.ts:227`).
Optional questions and persisted Yes/No/Unsure answers belong in the proposal. Yes never converts
an unavailable check to passing evidence.

### 10. Cost and rounds per accepted change

Count failed, stopped, rejected and review work too, divide all spend by accepted changes, and show
role/model breakdowns, revision rounds and budget pace. Keep unsuccessful buckets visible. With
no accepted changes the ratio is unavailable.

Writer/reviewer ticks already carry attribution (`contracts/src/oversight_contracts/events.py:358`,
`backend/src/oversight/runtime_accounting.py:68`). Fold individual costs and resolution events;
never sum cumulative `spent_usd`. Main keeps agent totals, not these aggregates
(`frontend/src/state/applyEvent.ts:175`). Live/replay derivation needs no addition; optional archived
change/role totals and rounds belong in the proposal. Unknown usage/unpriced Baseten work stays
unknown, never free. The cumulative product Meta ledger is separate from the bench ledger.

### 11. One plan, server-computed zones and task queue

The person reviews one editable plan; independent subtasks can then run in two slots. The server
computes zones, keeping import/call cycles together and cross-zone edges few, and checks that
their impact cones do not overlap. The model writes subtask text only. Dependent work starts after
its prerequisite merges. Use one agent when no independent split exists. Queue dispatch waits until a slot is actually released.

Main validates/expands supplied zones, not partitions (`backend/src/oversight/changes/_zones.py:40`).
Reuse that after deterministic cycle contraction; uncertain calls and same-file zones still need
conservative handling. Plan/queue routes remain 501 (`backend/src/oversight/api/commands.py:30`);
finishing alone does not free a retained worktree's slot. Plans reserve exactly two subtasks and
require both assigned agents while running (`contracts/src/oversight_contracts/plans.py:60`).
The Oct 28 proposal includes subtask dependencies and a relaxed plan validator for sequential
starts and one-agent fallback; implement them only after that contract extension is approved.
A partition heuristic does not prove independence; speed claims wait for the split bench.

### Lead agent

After items 8 and 11 are on main, add a thin lead layer on item 11 so proof 2 can start many
agents without the person starting each one. Hosted Muse Spark writes the plan and subtask text
and starts agents through public commands, using the reserved lead routes and `started_by`.
The server still computes zones and owns the task queue. The lead never merges or reviews and
sees only what the person can see. This supersedes its earlier deferral to Next.

### Saved agent flows

After the lead, extend package 12's shipped Start an agent here with a short list of repository
files. Each flow has a name, task text, a target (selected node, selected change, or files changed
since the last commit) and expected checks. The person picks a flow, sees the filled task text,
may edit it, then starts the agent. Its result uses the same card, rules and evidence as any change.

Start with three flows: write tests for the selected function or changed files; seek a
counterexample to the selected change; explain the selected change from its facts. Counterexample
search is a light version of the red agent, whose broader workflow stays in Next. Filling task
text needs no contract change; a separate role or tool limits go to the maintainer first. The
picker changes the look, so the maintainer sees a shot before merge.

### 12. Examples before code

When task text, tests and pins leave ambiguity, or the task touches a large cone or a pin, offer
one Muse Spark call proposing three to five distinguishing examples. The person confirms, edits
or rejects them before coding; confirmed examples become the task's done-when criteria. Offer at
most three alternative meanings; beyond that, ask for clearer task text.

Today's form starts coding directly (`frontend/src/agents/TaskBox.tsx:134`). Enforce a no-edit,
one-call examples phase in the server, then carry confirmations into the task. Optional task/example
fields belong in the proposal. Unconfirmed prose is not acceptance evidence. This is selective,
not an extra call for every task; a confirmed example's result needs no model judgment.

### 13. Pinned examples and graph lenses

After a choice, offer an ordinary generated repository test retaining input, expected result,
confirmer and originating task. Pins can be amended; breaking one fails the rules. One control
switches Tests, Hubs and Pinned examples; clicking a gap starts a task with its zone.
Use neutral work light and show the maintainer a shot before merging. Text rules remain Next.

Reuse test nodes/caller counts/files/task entry; main has no pins or lens registry
(`frontend/src/graph/elements.ts:94`, `frontend/src/graph/camera.ts:197`). Optional pin provenance
belongs in the proposal. The Cost per node lens was dropped: cost is measured per call and per agent,
so a node lens would present an allocation estimate as a measurement. The cost-per-node field in
usage events was declined too. Do not treat a file-naming convention alone as confirmed-pin enforcement.

### 14. Node card explained by examples

The node card first shows the test source where the function is called,
labeled as source. Real argument values wait for item 4's recorder; then distinguish observed test
inputs/results from proposed or confirmed examples using item 13's pins. Propose optional node
examples with origin so the person can tell source text from recorded execution.

Main opens node source/diffs (`frontend/src/app/view.ts:99`); the palette (`535e6f6`) adds navigation,
not runtime observations. Static edges carry no argument/result values
(`contracts/src/oversight_contracts/graph.py:45`). Source excerpts are the first delivery, not
observed executions. Capture excerpts and later recorded example data for replay: recordings need
not contain unchanged source. This is not a general runtime tracer.

### 15. Shape guard

Ask about duplicated logic instead of reuse, new import cycles/module dependencies, and functions
growing past a size threshold. Build a deterministic before/after analyzer over captured AST/source
and graph data. Spans/import edges exist (`backend/src/oversight/graph/_workspace.py:77`,
`backend/src/oversight/graph/_link.py:129`), but no shape producer is wired on main.

Propose optional findings, evidence and completeness, then render card questions. State dynamic
or unsupported cases. Size remains a review signal under AGENTS.md, not an automatic rejection
gate or learned risk score.

### 16. Bounded one-hop context for initial tasks

Give the agent a fixed-order slice of target signatures, direct callers/callees and module constants,
about 1,500 tokens at most, explicitly listing omissions. Adopt only after the bench shows fewer
steps and tokens with tasks still solved. The server builds this context as text, never a graph tool
for agents, following AGENTS.md and the existing Request changes context. No contract addition is needed.

Revision callers/siblings already work (`ee92691`, `backend/src/oversight/changes/_neighbors.py:23`,
`backend/src/oversight/changes/_revision.py:26`). Reuse ordering/uncertainty labels, add callees/constants
and a global budget for initial context. Do not rebuild package 11 or confuse a per-list cap with
a global token bound. This is a bounded exception to the human-facing graph, not general agent
access or a two-hop expansion.

### 17. Run rule

A test failed, its checks were weakened later, and it no longer fails in a subsequent complete run:
that sequence deserves attention. Source detector counts stay neutral; the diff alone is not an
alarm. Include this in the video only if a real run supplies the evidence.

The [historical precision measurement](../tools/shortcut_corpus_report.md#labeled-oversight-evaluation) on this repository's history reduced 101 findings to 24, with signal in 11/20 labeled identities and four unlabeled identities, so it does not establish precision for all 24.

The core/event adapter exist (`aaa9af8`, `backend/src/oversight/changes/_runrule.py:1`), but facts
publish no temporal findings (`backend/src/oversight/changes/_facts.py:375`). Wire the reserved
assertion-loosening case first, with matching facts and ordered references
(`contracts/src/oversight_contracts/review.py:253`). The core also handles skipped/deleted tests:
absence of failure does not prove passing. Never infer recovery from truncated failure lists or
force broader kinds into the current wire shape. First wiring needs no addition; the Oct 28
proposal adds the run-rule finding kind for removed, skipped and deleted checks. That extension
must be approved before publishing the broader cases.

<a id="18-remaining-small-packages-if-time-remains"></a>

### 18. Remaining small packages

Package 11's revision callers/siblings (`ee92691`) and package 12's Start an agent here (`c1c19c6`,
`frontend/src/agents/StartAgentHere.tsx:10`) are complete. Keep them for traceability, not new work.

Package 13 is Muse Voice Transcribe task dictation, last day only, never merge approval. Its
separate audio-upload command/response is included in the Oct 28 proposal as an endpoint answering
501 until built; message/pause/resume reservations do not cover it. Contract implementation still
requires approval of that proposal.
Package 14 is person-to-agent messages, pause/resume and replay timeline. Message/pause/resume routes are
reserved and still 501 (`backend/src/oversight/api/commands.py:51`); playback already consumes ordered
events. Reuse those interfaces/local controls without claiming messaging or seeking already works.
Earlier-today history belongs to item 8; usage views belong to item 10.

### Standing grants

Added by the maintainer on 2026-10-29. Oversight remembers what the person authorized, so it does
not ask again for the same thing. Rules pass or wait with evidence, Muse Spark Open's decision can
only add a wait, and the person's grants can only remove a wait the person could accept. A grant
is made only from the person's answer to an inbox entry, with a narrow scope that expires with the
run; it waives possible callers not followed or a protected path, never tests, conflicts, test
integrity, cross-review or a decision question. Grants are listed with Revoke, recorded on every
change they merge, and counted apart from questions in proof 2. Plan, contract proposal and order:
[standing-grants.md](plans/standing-grants.md). Proposed pitch line, for PRODUCT.md and the video
once approved: "Rules check. The open model can only stop. Only you can let go, and Oversight
remembers what you let go."

### Shipped editor language highlighting

Non-Python highlighting shipped on main in `e1e5599` and `b238a6c`: YAML, Markdown, JSON, TOML,
shell and INI use language by extension in the file view and live diff, without language-service
workers (`frontend/src/editor/language.ts`, `FileView.tsx:193`, `LiveDiff.tsx:266`). Files producing
no graph nodes have a discreet “No impact analysis” header label. This improves file reading
without claiming graph analysis for another language.

### Delivery, graph polish and bench

**Delivery (package 9).** After item 2 lands, prioritize a static “Would you approve it?” challenge:
three changes, local Approve/Reject choices, findings and score. This is proof 1, the submission
link and the opening of the video. It must work at phone width; show the maintainer a phone-width
shot before merge so judges can try it from a phone. Main has verified backend-free
replay (`a0a4624`), with two bundled proposals, no third challenge or scoring. Use replay cuts and a
companion challenge manifest; no product contract addition. Label planted bugs and preserve real-run
provenance only for unmodified recordings. Automated 1080p replay takes and a 720p compression check
remain delivery work, not another roadmap.

The maintainer chooses the video script Mon Nov 2: start with a real clean-looking change and green
tests, ask for a decision, reveal an unrequested result and measured cone, then scale with real-run
counts. Keep editor/file view/editable side out of the video. Replace the level 2 fallback only
after three clean one-command runs without manual fixes. Robustness/takes freeze Tue Nov 3 at
20:00 ART; submission target Wed Nov 4 at 20:00 ART.

**Graph polish (package 8).** Continue readability/performance within DESIGN.md's tokens, saved
layout and single graph. Dot-field and palette exist; `535e6f6` includes Ctrl K navigation. No new
visual system, focus-stealing alerts or lens colors. Without WebGL the background stays black.

**Task bench (package 10).** Eight tasks, independent hidden checks, reference patches, API-only
execution through Oversight and durable reservations already exist (`9a1772f`;
`tools/eval/README.md:3`). Earlier ten or 15–20 task counts are not present coverage. Arms are in
progress on `tools/bench-arms`; main keys attempts by task/profile (`tools/eval/ledger.py:112`).
Keep charges, unknowns and reservations in one ledger keyed by task, profile, arm and repetition;
never reset it or change output directories to manufacture another attempt.

Declare criteria before paid execution. Combined paid bench work has a US$20 ceiling within the
US$150 budget; agent B's Baseten cost stays unknown until its price per million tokens is set in
`.env`, and enforcing the cap requires that explicit tariff. Pilot one task per arm and project
total cost; cut repetitions before arms if over cap. Higher reasoning runs only if the rest fits.
This docs front runs no paid bench or provider
call. Required comparisons:

- Behavior detection and task-intent classification: freeze 10 unrequested-difference changes
  (none let through) and 10 requested-difference changes (at most two needless questions).
  The maintainer labels expected behavior before detector output. Compare tests only, Muse Spark
  diff review, and behavior diff plus advisory call on the same patches. Report detection and
  classification separately; include unprobed cases in denominators; exclude development examples.
  Add an arm on the same 20 frozen patches replacing Muse Spark's advisory call with Spark Open's
  constrained decision. Alongside detection and classification, report calibration: whether a
  probability above the threshold means the decision was right, if the probe-first condition in
  item 9 supports probabilities. Choose the threshold from development examples, never the 20.
  This arm shares the bench's US$20 cap and waits for the configured Baseten tariff.
- Harness: eight tasks, three repetitions per task/arm, randomized arm order, same model/reasoning/
  limits. Primary outcome is solved tasks; also report turns/tokens/dollars/wall time. Current tools
  versus pack; current tools at higher reasoning only if the cap allows.
- Plan/zones: graph split versus one agent doing both tasks and naive split; joint correctness,
  wall time, tokens and dollars. Parallelism alone does not establish speed.
- One-hop context: steps, tokens and solved tasks with and without the bounded slice.

PRODUCT and video wording for these model roles will be proposed to the maintainer later.
