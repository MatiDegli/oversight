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
   decisions the person had to make. Each task gets hidden checks, frozen before the run without
   seeing agent output. The same run reports two arms judged by them: each agent's first proposal
   with passing tests, merged as it is (without Oversight), and what reached main (with
   Oversight). Each arm reports correct changes on main, regressions that got through, questions
   per accepted change, questions that were not needed, and cost and wall time per correct change.
3. **Unrequested behavior among green patches.** From that same run, measure the share of patches
   with passing tests whose behavior diff reveals a difference the task did not request. Fix the
   reporting rule before measuring: if the share is low or the sample too small, tell the scale
   story without a percentage. Do not repeat the run to improve that result.

Checkpoint: items 1, 2, 4 and 9, the behavior-diff bench and proof 1 must all be on main. The
cut line is drawn as soon as they are, whatever the day. Sat Oct 31 at 23:00 ART is only the
fallback deadline: if any is missing then, cut work from item 12 down and tell the maintainer
what was cut.

## Decided queue

### Hackathon priority (2026-10-30)

The maintainer set this order on 2026-10-30, ahead of the queue below. It has no dates: take the
next item by real progress.

1. Evidence for an added optional parameter (bench tasks b02, b06, b08, b10, b12 and b18), inside
   the bench evidence fix: the most common change must not end in Needs you.
2. Item 10 with agent-hours, project active hours and parallelism. Proof 2's closing line ("this
   many agents for this many hours") depends on it. (The maintainer confirmed proof 2 on
   2026-10-30.)
3. Reasons in words on the card instead of "probe unavailable"
   (`backend/src/oversight/changes/_behavior_publish.py:166`), from fixed templates over the
   evidence, never written by the model.
4. "Waiting for model (retry n/5)" on the card, from the call's own data (five attempts,
   `backend/src/oversight/agents/client.py:163`); a provider halt says so instead of "working".

This is the working home for everything beyond level 2. The maintainer set this order on
2026-10-28. When capacity opens, take the first unfinished item that can proceed; dates do not
supersede priority. Level 2 closed on Oct 27; its demo remains the fallback. Queue numbers replace
the old idea numbers. Package numbers identify the Oct 27 work packages. Keep the item numbers:
work through 1–11, then the lead agent, saved flows, 12, 13, 17 and 18; take 14–16 only if time
remains. The two additions after item 11 are unnumbered.

States were checked against main at `454f602`, including its code and merge history. Queued rows
describe what remains, including where part is already on main. A backend or pure core on main
does not mean its routes or UI are wired. The descriptions give code evidence and boundaries.

Contracts 1.8.0 landed in `bb9dc43`: E6's failed model attempts and item 11's dependency plans
(P1–P5). E6's producer landed in `06c34a5`; the waiting/retry card remains unwired
(`frontend/src/state/applyEvent.ts:28`).

**In contracts proposal:** **Yes** means included in the requested Oct 28 proposal, not approved
or already reserved; **No** means no addition is needed; **Pending** means a contract question
needs the maintainer's decision. Existing reservations are noted below. This document does not
amend [the approved proposal](proposal.md) or authorize edits to `contracts/`.

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 1 | Facts and a summary from events (package 3) | On main — summary producer `027ec36`, review rendering `9cdd02a`; facts row/steps `ae9822d` | Yes — summary; report check needs no field |
| 2 | Behavior diff | Backend on main — opt-in publication `e72fcc7`, shared-input comparison `647d545`, methods/getters/setters `2cfe760`, project-object arguments `8a4a2ec`, unprobed reasons `f44dd62`; review rows and graph count chips with sample popovers built (`frontend/node-chip`) | Yes |
| 3 | Agent harness pack | On main — opt-in pack `42976ee`, runner wiring `b4446cc` | No |
| 4 | Measured impact cone | Backend on main — caller evidence `1c50382`, bounded GET /impact `920e930`; measured graph display remains | Yes |
| 5 | Live overlap between agents | Queued — card facts/change-lifetime reads remain; latest focus and last-read node only at `454f602` | No |
| 6 | Fails before, passes after (package 4) | On main — base setup/import errors are unknown `b786f6c`; isolated probes `19443e8` | No — conservative classification uses existing fields; exception type declined |
| 7 | Cross-review and automatic Request changes (package 2) | On main — one-round revision `7da6fa9`, independent reviewer selection `045f589`; manual review route still 501 | No |
| 8 | Merge by rules, follow main and Revert (package 1) | Partial — transaction `73b4f65`, barriers `7aec7d5`, merge-mode HTTP `0e21fdf` wired; start capability `1824a46` keeps activation closed; protected-path evaluation/capture `f8d720c`/`31b45f2`, spend evidence `085c555`, inverse-merge primitives `5bf57e7`/`5aa5680`, Revert preflight `88a54f4` and internal service `454f602` on main; Revert route/UI, follow main and preemption remain | Yes — protected_paths rule in a new policy profile version; merge/Revert shapes reserved |
| 9 | A change card with three states | Partial — decision producer `3786310`, grouped witnesses `3068b81`, answer guards `2efd586`, inbox projection `f4ab346`; full UI/rules state remain | Yes |
| 10 | Cost and rounds per accepted change | Partial — usage fold `43f3bb8`, archived change totals `bf36052`; agent-hours, dated prices and aggregate UI remain | Yes — stored aggregates; event-derived totals need none |
| 11 | One plan, server-computed zones and task queue (package 6) | Partial — standalone queue `c820fbf`, dependency contracts `bb9dc43`, plan service/routes and internal dependency dispatch `efd592f`, metered single-subtask generation `f907e41`; partition core `deb7148` remains unwired and plan UI remains | Yes — subtask dependencies and a relaxed plan validator for sequential/single-agent starts |
| — | [Lead agent](#lead-agent) | Queued — after items 8 and 11; lead routes still 501 at `454f602`; internal lead-supplied plan drafts supported by `efd592f` | No — lead routes and `started_by` reserved |
| — | [Saved agent flows](#saved-agent-flows) | Queued — picker absent at `454f602`; after the lead, extends package 12 `c1c19c6` | No while filling task text; a distinct role or tool limits need maintainer review |
| 12 | Examples before code | Queued — contracts reserved `649ff0d`; no examples phase on main `454f602` | Yes |
| 13 | Pinned examples and graph lenses | Queued — pin contract reserved `649ff0d`; no pins or lens control on main `454f602` | Yes |
| 17 | Run rule (package 5) | Backend on main — assertion findings `aa4e658`, removed/skipped checks `071844e`; review rendering remains | Yes — run-rule finding kind for removed, skipped, deleted checks |
| 18 | Remaining packages 11–14 | Partial — run controls `d27875a` and message/pause/resume HTTP `d085748` on main; dictation still 501, run-control UI and replay timeline remain; 11 `ee92691` and 12 `c1c19c6` complete | Yes — separate dictation endpoint, initially 501; message/pause/resume reserved; replay/input controls local |
| — | [Standing grants](#standing-grants) | Backend on main `93a3664` — run-local possible_callers grants, module/run scope and revoke wired; rules activation still closed `1824a46`; grants UI and protected-path grants remain | Contracts 1.7.0, approved 2026-10-29; the creation bundle moved to 1.8.0 — grant events, `ApproveChange.grant`, `ChangeResolved.grants`, revoke route; moved again to 1.9.0 on 2026-10-30 when failed model attempts and dependency plans took 1.8.0 `bb9dc43` |

### Only if time remains

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 14 | Node card explained by examples | Queued — caller evidence `1c50382`; node example view absent at `454f602` | Yes |
| 15 | Shape guard | Queued — contract reserved `649ff0d`; no structural comparison producer on main `454f602` | Yes |
| 16 | Bounded one-hop context for initial tasks | Queued — initial slice absent at `454f602`, waits for bench; revision callers/siblings `ee92691` | No |

**Not ranked, goes in regardless:** delivery (package 9; static replay `a0a4624`, challenge
`99a57de`, phone practice `0c884f6`/`b7ea554`/`6ce0d19`, recorded evidence `e621acb` on main), graph
polish (8; scale layout `04f0a41`, life layer `8fad4c2`/`ff80c6f`, exact edges `19a94ce`, camera fit
`37c40b9`/`b860ef4` on main, polish continues), and the task bench (10; arms `9ad1ecf`, frozen
patches `724b5c9`, detection arms `75b30f8`, sensitivity gate `cc52a78` and harness pair `ad5de55`,
split measurement `74b5791`, send accounting `cc023c8`, probe-cause reports `507ce31`/`25d6ea3` on
main; the gate does not establish a completed paid run).

## Undecided candidates

These retain useful ideas from the old inventory without approving a front or changing the freeze.
None is queued: nothing is built until the maintainer orders level-3 work (2026-10-30).

- Native sandbox, the first level-3 candidate (maintainer, 2026-10-30). "A sandbox" is on AGENTS.md's
  out-of-scope list; lifting it needs the maintainer's decision. Goal: safety without slowing
  agents. The sandbox bounds side effects, not what an agent does in its task; normal work runs
  with no prompts.

  1. On by default for agents; off only by the person's explicit choice. The person's own commands
     are never sandboxed. Linux first, with bubblewrap or the unshare already in
     `backend/src/oversight/workspace/isolation.py` (today network and PID only). On Windows and
     macOS the card says "no sandbox on this system" and never implies one.
  2. The worktree is the agent's world. Free, with no prompt: writing in its own worktree, reading
     the repository, running tests, linters and builds, and installing from allowlisted registries
     (PyPI, npm) or from shared read-only uv and pnpm caches. Blocked: writes outside its worktree,
     other agents' worktrees included; credentials (SSH keys, .env, key files); any other network
     egress; global installs.
  3. One tier per role: writers as above; reviewers read-only; probes and test runs strictest, with
     no network and writes only to a temporary directory; the person unsandboxed.
  4. Fast exit: a blocked action returns a clear error to the agent ("network to X blocked"), and
     the card shows "Needs you: asks for access to X". One click approves it, remembered per
     repository, so the allowlist grows with use. The lead never grants an exit on its own.
  5. Metric: exits requested per accepted change, beside questions per accepted change. A task type
     that keeps hitting the boundary means the allowlist is wrong; the target is zero in normal
     work.
  6. Division of labor: Oversight's evidence guards what code reaches main; the sandbox guards what
     code does while it runs.

- Progress made visible, from real data only (maintainer, 2026-10-31). No bar or percentage
  without a real denominator. A sketch round follows the map sketch; nothing is built before the
  checkpoint.

  1. Evidence steps on each change card: tests, fails before and passes after, behavior diff, cone,
     cross-review and decision, each shown as done, running or pending from existing events.
  2. Collapsible agent steps with their durations, from `agent.step` events.
  3. A plan checklist from item 11's per-subtask states, for example "2 of 4 merged".
  4. Progress bars only against a real bound: the agent's time limit, spend against the budget,
     model retries (n/5), and proof 2's queue, for example "7 of 16 tasks done".
  5. Later, and only with a contract change: a todo list the agent writes, labeled as the agent's
     claim.

- Benchmarks view: a read-only view of the bench and proof results inside Oversight. Until it
  exists, the README and the demo video carry the proofs.
- Linux on demand: with the Tauri shell on Windows, git, Python and Node run natively, and only
  what needs the Linux kernel (isolated test runs, the sandbox) goes to WSL, which starts on demand
  and stops when idle.
- Resume a halted agent in the same conversation and worktree, through the level-3 resume route
  (live, `backend/src/oversight/api/commands.py:307`).
- Budget reminders with steps reserved for `finish`, and a bounded history with a visible handoff.
- Fresh revision context built from facts, distinct from the existing retained-history revision.
- No repeated automatic revision for an unchanged failure: before cross-review or rules follow-up
  starts a round, compare the test and assertion with the last failure and the agent's edits since
  then. A changed node reaches the test if the test can reach it through the graph's call edges,
  or it appears in that test's executed nodes when recorded. If nothing relevant changed, the card
  asks the person. This is deterministic, with no model call; if neither source can tell, ask
  instead of starting another round on a guess.
- Behavior diff for methods was approved on 2026-10-30 as part of the bench evidence fix and is
  no longer a candidate; see DECISIONS.md.
- A dependency guard for package existence, license and vulnerability evidence.
- Native workflow candidates from the maintainer (2026-10-29), none queued. The cut line, drawn
  once the checkpoint list is on main, comes before any of them is considered.
  - Resource admission: the person's cap on running agents stays the limit. Memory and spend can
    only hold a start below that cap, never raise it, and a held start shows its reason, for
    example "Waiting: memory at 88%, 2 test runs ahead". Thresholds are deterministic; no model
    decides. An already-running agent waiting on a shared resource shows the reason in words on
    its card, built from the scheduler's own data, for example "waiting for a test slot (2 queued)".
    Use the same form as a model wait, for example "waiting for model (retry 3/5)".
  - A start-box warning: when the person starts an agent by hand and its target lies inside a
    running agent's impact cone, the start box says so. It only warns; it never blocks or
    reassigns. Plan zones and the cone check are item 11, and live overlap is item 5; only this
    warning is new.
  - Time per stage for each change: agent work, test runs and waiting for the person's decision,
    shown beside cost and rounds (item 10).
  - A visible merge queue. Item 8 already tests a change on the combined result when main moved
    since its base (`backend/src/oversight/changes/_merge.py:65`, `combined_tests_pass` at :120);
    only the visible queue is new.
- Export reviewed revisions/comments as decision pairs; no training during this event.
- A person-approved plan before editing protected areas; item 8's deterministic veto is separate.
- Person-drawn work zones that limit edits, with a scope-request tool, warn mode and zone growth by approval; item 11's server-computed zones and the reserved `within_zone` rule are separate.
- A TypeScript graph adapter; another language/dependency needs a separate scope decision.
- Outside agents reporting through hooks or MCP, after the hackathon's Meta-only product scope.
- Team integrations, evidence attached to pull requests, and exporting any run as a shareable replay.
- [Scale and depth](../DESIGN.md#scale-and-depth): The map view and folder groups are left out of the current level. They come after the Conversation tab, milestone pulses and life layer part 2, built only from a sketch the maintainer approves (maintainer, 2026-10-31).
- A Tauri shell, a local model in slot B, and named editor cursors.
- [Repository memory](#repository-memory): what Oversight remembers about a repository that
  search and the graph cannot give. A candidate from the maintainer, not queued; nothing is built
  before the freeze.

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
- After the hackathon: keys typed into a packaged app's Settings, write-only and kept by the
  local backend; needs the secrets rule changed, a contract change and a reviewed security plan.

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
- A model training room: after the hackathon and outside Oversight's scope; declined for now.
- Native fine-tuning: after the hackathon and outside Oversight's scope; declined for now.
  It would depend on TRL, Unsloth or Axolotl (Apache-2.0) or torchtune (BSD-3-Clause),
  and Muse Spark cannot be fine-tuned through the API.

## Item descriptions

### 1. Facts and a summary from events

Give the person at most three lines covering the request, actual edits, tests added/run, other
results changed and cost. Derive them from task/status, change, execution and usage events, never
the author's `finish` prose. Missing paired results must say they were not compared.

Package 3 already publishes source facts and execution references. The shared facts row and recent
steps landed in `ae9822d` (`frontend/src/review/ChangeFactsRow.tsx:76`). Recognized report claims
such as “tests pass” already produce contradictions against the last agent run
(`frontend/src/review/ReviewFacts.tsx:66`, `backend/src/oversight/changes/_reviewfacts.py:209`).
Reuse these checks: they do not understand arbitrary prose and need no new field. The optional
summary contract landed in `649ff0d`; the event-derived producer (`027ec36`) and review rendering
(`9cdd02a`, `frontend/src/review/FactsSummary.tsx:4`) are on main. Do not put report contradictions
into `ChangeFacts.integrity`, whose kinds describe test-source observations.

### 2. Behavior diff

Show a difference count and up to eight input/before/after rows per changed function on its node
and in review. Run base and candidate in separate fresh processes on identical bounded inputs:
literal test arguments, plus `c-1`, `c`, `c+1` for integer constants in either module, and `0`/`-1`
for integer arguments. Bind by parameter name; compare typed values and supported exceptions.
Execute each differing input twice in total on each side. Results varying between runs are
unstable, not an established change.

The core landed in `174c0ce`; contracts 1.5.0 (`649ff0d`) reserved its optional per-node evidence,
including unstable/not-probed outcomes. Publication is wired behind `OVERSIGHT_BEHAVIOR_DIFF`
(`e72fcc7`, `backend/src/oversight/changes/_proposal.py:328`). Shared-input comparison landed in
`647d545`; review rows and per-node count chips with up to eight sample rows are built
(`frontend/node-chip`). Reuse the demo interpreter, asynchronous bounded runner, guarded base
checkout and C3 session isolation
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

The opt-in pack is on main (`42976ee`), including hash/uniqueness checks and bounded observations
(`backend/src/oversight/agents/tools.py:425`, `backend/src/oversight/agents/_observations.py:14`).
Runner failure retention is wired too (`b4446cc`, `backend/src/oversight/workspace/suite_guard.py:68`).
Preserve failures before the runner discards text. No contract addition or tool is needed. One
setting lets the bench compare both arms with the same model, reasoning and limits. This does
not replace independent server proposal tests.

### 4. Measured impact cone

Distinguish callers with observed result changes from those only possibly affected. Extend item 2
through the external pytest reporting plugin: run the same tests on both implementations, record
inputs/results at caller boundaries, match by test/caller/order, and compare only equal inputs.
Tests construct the objects; do not serialize/rebuild arbitrary objects to invoke callers.

Bounded caller recording and optional measured-cone evidence, including unmatched calls and
incompleteness, are on main (`1c50382`), behind `OVERSIGHT_MEASURED_CONE`
(`backend/src/oversight/changes/_proposal.py:342`). GET /impact serves bounded graph cones
(`920e930`); measured caller highlighting/list rendering remains. Both runs
require session network isolation, never open-network execution. Unsupported or unmatched calls
are not compared; other nodes stay “possible, no change measured”. This is neither per-test
coverage nor proof that all callers behave identically.

### 5. Live overlap between agents

Identify same-node edits on the node and both cards. If B has read something A is changing, say so
on B's card and retain observed reads for the whole change. Edits inside another agent's cone
become a fact at review. Never interrupt the person or move focus for overlap. Overlap warnings
name the shared nodes and never block on their own: they warn, and the person decides.

Reuse `pending-both` (`frontend/src/graph/styles.ts:302`), pending-node sets and live/proposal cones.
Add pure selectors and change-lifetime read sets to the reducer; main retains latest focus and
one last-read node per agent (`frontend/src/state/applyAgentEvent.ts:42`), not change-lifetime reads.
No contract addition; replay must agree with live runs.
Search focus is capped, so observed reads are not a complete audit. Once item 18 supplies messages,
notify the later agent as a separate turn; notification in its next tool result remains Next.

### 6. Fails before, passes after

Tell the person whether a new test demonstrates the change or merely failed to import or set up
on base. Keep package 4's case discovery, selected base/head probes and review output (`b7122ef`);
C3 already requires isolated probes (`backend/src/oversight/changes/_probes.py:229`). Evidence
classification landed in `b786f6c`.

Base import/setup errors now count as unknown with their diagnostic, using existing probe
results/problems (`backend/src/oversight/changes/_beforeafter.py:58`); they no longer establish
failure on base. The merge-alone evaluator must never use them as evidence of failure on base.
Build this before merge-alone can activate, with no contract change. That prerequisite is met
by `b786f6c`. An exception-type field was declined for the
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

`PRODUCT.md`'s success sentence, set by the maintainer on 2026-10-30, names the person's approval or a rule
they set as the authority for every merge; rules mode stays closed by default until its remaining pieces land.

Main has policy profiles, the P2b-1 scheduler (`9f70622`), reservation (`5dac67c`), merge transaction
(`73b4f65`) and final race barriers (`7aec7d5`). Reservation and the transaction are wired through
`backend/src/oversight/changes/_manager.py:119`; the mode remains closed because isolated start
capability is not connected (`backend/src/oversight/changes/_mode.py:23`). GET and PUT /merge-mode
are wired (`backend/src/oversight/api/commands.py:211-220`). Remaining work follows the maintainer's
order: isolated start capability first, then the remaining Revert recovery/history and UI before
enabling the mode. The Revert route is live (F8 `69aaee8`), including the preflight check (`88a54f4`);
bounded history reads are wired (`0e5305a`). Protected paths have an evaluator (`f8d720c`). C3 landed
in `19443e8`; rules and probes require it.
Human approval already retests moved bases
(`backend/src/oversight/changes/_merge.py:45`), and revision can merge main into the retained branch.
Reuse those paths; rules must test the combined result even when base has not moved.
Enable cross-review with the other model before merge-alone can activate; the merge-alone evaluator
must consume its result and require no blocking finding, alongside the deterministic checks.

Manifest/lockfile, CI, test-configuration and security-path edits always wait for the person and
receive cross-review. The maintainer included a named `protected_paths` rule in the Oct 28 contract
proposal, with a new policy profile version: `RuleId` is closed and profiles validate against it
(`contracts/src/oversight_contracts/common.py:44`). A server-only veto would hide the policy reason.
This approves its inclusion in the proposal, not a change to the current contracts. The separate
contracts 1.5.0 delivery reserved the rule (`649ff0d`). Revert and
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
The answer-to-another-agent link is now reserved in contracts 1.6.0 (`a13ef3f`) and wired in
`7c989e2`. Inbox projection landed in `f4ab346`, including delegation recovery (`8c13033`) and
one pending decision per change (`eef7daa`); the full inbox view remains.

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

Reuse target-aware notes (`frontend/src/review/CrossReviewNote.tsx:11`). The constrained decision
client (`15dcff4`), review-card producer (`3786310`), asked-for summary (`abc281d`) and persisted
answer guards (`2efd586`) are on main; grouped witnesses followed in `3068b81`. The live review
UI still lacks the full card/inbox controls, and assessments/Revert are not folded into displayed
state (`frontend/src/state/applyEvidenceEvent.ts:36`). Optional questions and persisted answers
were reserved in `649ff0d`. Yes never converts an unavailable check to passing evidence.

### 10. Cost and rounds per accepted change

**Agent-hours come first:** proof 2's closing line ("this many agents for this many hours") uses them. The
intervals of one agent are unioned, distinct agents add up, and a lead that only waits for its agents adds
nothing. Use observed intervals, never fixed time buckets: each agent's own working intervals come from its
agent.status events, where starting, working and revising count and ready_for_review, halted, done, stopped and
paused do not. A review is one bounded call that starts when the author's run ends after proposing, so its
interval runs from that agent's ready_for_review status to change.reviewed and it counts as its own actor. Also report project active hours (the union
of all intervals) and parallelism (agent-hours divided by project active hours). The lead has no producer yet
and lead.status cannot tell waiting from working; until its contract can, lead hours are unavailable and its
waits are never counted.

**Costs are list-price equivalents, not a bill:** every cost names its dated price source. The source is
Meta's official pricing page. The repository stores the price table extracted from it, with the page's URL and
retrieval date, never a copy of the page; on 2026-10-30 it listed
muse-spark-1.3-contributor at US$0.10 input, US$0.002 cached input and US$0.20 output per million tokens, and no
price for Muse Spark Open, so Baseten work is unpriced. The view applies the stored price to each call's token
counts. The budget brake keeps its own conservative ledger, which prices cached input as fresh, and the view
never presents its figures as that ledger. A call whose cached count was not reported has a cost range; only
its lower end enters the known subtotal.

**Coverage:** missing usage, activity or price shows the known part as a lower bound and the total as
unavailable, never a smaller total presented as complete. There are three levels: complete; missing metrics
(unpriced work, or an actor with work but no interval); incomplete (an integrity failure). Null is unknown and
zero is zero; zero usage costs zero even when unpriced. Unpriced rows sort last, and "—" means unknown only.
A sum of period figures adds their known subtotals and is labeled a lower bound.

**Grouping:** by team, agent, model and role, split by hour or day. A team is a lead and the agents it started
(`started_by`); agents the person started form their own group. Periods follow local wall time, so a
daylight-saving change gives a 23- or 25-hour day. Intervals are clipped at period edges; a cost falls in the
period of its usage.tick and is never prorated. Group rows add up only when every group's activity is complete
and their sum matches the global figure; the total always comes from the global figure. A comparison between
periods uses exact values, never rounded display text, and shows a number only when both sides are complete.

Count failed, stopped, rejected and review work too, divide all spend by accepted changes, and show
role/model breakdowns, revision rounds and budget pace. Keep unsuccessful buckets visible. With
no accepted changes the ratio is unavailable.

Writer/reviewer ticks already carry attribution (`contracts/src/oversight_contracts/events.py:358`,
`backend/src/oversight/runtime_accounting.py:68`). Fold individual costs and resolution events;
never sum cumulative `spent_usd`. Main has the committed usage/rounds fold (`43f3bb8`) and exposes
change/role totals and rounds in archived GET /changes/{id} (`bf36052`,
`backend/src/oversight/changes/_view.py:143`); GET /agents/{id} and bounded GET /history are wired
too (`bf36052`, `0e5305a`). The frontend still keeps agent totals, not the aggregate usage view
(`frontend/src/state/applyAgentEvent.ts:52`). Agent-hours, dated list-price accounting and the
aggregate UI remain. E6 now emits failed model attempts (`06c34a5`), but those events alone do not
supply activity intervals or retry-card rendering. Live/replay derivation needs no addition;
optional archived totals/rounds were reserved in `649ff0d`. Unknown usage/unpriced Baseten work stays
unknown, never free. The cumulative product Meta ledger is separate from the bench ledger.

### 11. One plan, server-computed zones and task queue

The person reviews one editable plan; independent subtasks can then run in two slots. The server
computes zones, keeping import/call cycles together and cross-zone edges few, and checks that
their impact cones do not overlap. The model writes subtask text only. Dependent work starts after
its prerequisite merges. Use one agent when no independent split exists. Queue dispatch waits until a slot is actually released.

Main validates/expands supplied zones (`backend/src/oversight/changes/_zones.py:40`).
Reuse that after deterministic cycle contraction; uncertain calls and same-file zones still need
conservative handling. A pure partition-feasibility core landed in `deb7148`, but has no production
completeness producer or caller (`backend/src/oversight/changes/_partition.py:1`). Plan routes are
live (`backend/src/oversight/api/commands.py:175` and `:230-247`). Queue routes are live, with the standalone
FIFO dispatcher wired (`c820fbf`); plan queueing and dependency scheduling remain. Finishing alone
does not free a retained worktree's slot. Contracts 1.8.0 (`bb9dc43`, P1–P5) now allow one
to six subtasks, validated dependencies and sequential/single-agent starts. Those schema changes
do not start or dispatch a plan (`contracts/src/oversight_contracts/plans.py:1`).
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
fields were reserved in `649ff0d`; the phase is not wired at `454f602`. Unconfirmed prose is not
acceptance evidence. This is selective, not an extra call for every task; a confirmed example's
result needs no model judgment.

### 13. Pinned examples and graph lenses

After a choice, offer an ordinary generated repository test retaining input, expected result,
confirmer and originating task. Pins can be amended; breaking one fails the rules. One control
switches Tests, Hubs and Pinned examples; clicking a gap starts a task with its zone.
Use neutral work light and show the maintainer a shot before merging. Text rules remain Next.

Reuse test nodes/caller counts/files/task entry; main has no pins or lens registry
(`frontend/src/graph/elements.ts:94`, `frontend/src/graph/camera.ts:197`). Optional pin provenance
was reserved in `649ff0d`; pin production and lenses remain absent at `454f602`.
The Cost per node lens was dropped: cost is measured per call and per agent,
so a node lens would present an allocation estimate as a measurement. The cost-per-node field in
usage events was declined too. Do not treat a file-naming convention alone as confirmed-pin enforcement.

The pin record is born with `kind` (only `example` for now) and a node anchor, so that a later text
rule is one new kind rather than a second pin family; `ExamplePin` in contracts 1.5.0 stays as it
is. A node anchor breaks on a rename: when a pin's anchor no longer exists, the pin shows as
orphaned, in the Pinned lens and as an inbox entry, and never vanishes silently.

### 14. Node card explained by examples

The node card first shows the test source where the function is called,
labeled as source. Real argument values wait for item 4's recorder; then distinguish observed test
inputs/results from proposed or confirmed examples using item 13's pins. Propose optional node
examples with origin so the person can tell source text from recorded execution.

Main opens node source/diffs (`frontend/src/app/view.ts:99`); the palette (`535e6f6`) adds navigation.
Caller inputs/results are now recorded by item 4 (`1c50382`); this node example view remains absent
at `454f602`. Static edges carry no argument/result values
(`contracts/src/oversight_contracts/graph.py:45`). Source excerpts are the first delivery, not
observed executions. Capture excerpts and later recorded example data for replay: recordings need
not contain unchanged source. This is not a general runtime tracer.

### 15. Shape guard

Ask about duplicated logic instead of reuse, new import cycles/module dependencies, and functions
growing past a size threshold. Build a deterministic before/after analyzer over captured AST/source
and graph data. Spans/import edges exist (`backend/src/oversight/graph/_workspace.py:77`,
`backend/src/oversight/graph/_link.py:129`), but no shape producer is wired on main.

Optional findings, evidence and completeness are reserved in `649ff0d`; the producer and card
rendering remain absent at `454f602`. State dynamic or unsupported cases. Size remains a review
signal under AGENTS.md, not an automatic rejection
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

The core/event adapter (`aaa9af8`) and facts producer are on main: assertion-loosening findings
landed in `aa4e658`, removed/skipped/deleted cases in `071844e`, using the kinds reserved in
`649ff0d` (`backend/src/oversight/changes/_facts.py:432`). Live review rendering remains absent
at `454f602`. Absence of failure does not prove passing. Never infer recovery from truncated
failure lists or force broader kinds into the current wire shape.

<a id="18-remaining-small-packages-if-time-remains"></a>

### 18. Remaining small packages

Package 11's revision callers/siblings (`ee92691`) and package 12's Start an agent here (`c1c19c6`,
`frontend/src/agents/StartAgentHere.tsx:10`) are complete. Keep them for traceability, not new work.

Package 13 is Muse Voice Transcribe task dictation, last day only, never merge approval. Its
separate audio-upload command/response was reserved in `649ff0d` as an endpoint answering 501
until built; message/pause/resume reservations do not cover it. The dictation route still answers
501 (`backend/src/oversight/api/commands.py:57`).
Package 14 is person-to-agent messages, pause/resume and replay timeline. Message/pause/resume
routes are live (`backend/src/oversight/api/commands.py:297-310`); playback already consumes ordered
events. Reuse those interfaces/local controls without claiming seeking already works.
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

Contracts 1.7.0 and the first backend landed in `93a3664`: possible_callers only, module/run scope,
run expiry, Approve creation and POST /grants/{id}/revoke. Grant checks are wired into reservation
and every transaction barrier. Creation requires a current rules-mode wait
(`backend/src/oversight/changes/_grants.py:147`), so closed rules activation still prevents live
creation/use; the frontend ignores grant events (`frontend/src/state/applyEvent.ts:30`). The full
grants UI, broader scopes/expiry and protected-path grants remain.

### Repository memory

A candidate from the maintainer, 2026-10-29. It is not queued, and nothing is built before the
freeze.

An agent finds with search what is written in code. It cannot find what is not written down, such
as the maintainer's decisions, invariants and the reasons behind them, or what it does not know to
look for. Search also enforces nothing and does not notice when a rule went stale. This candidate
covers only that gap. It never stores what search or the graph already give, such as where
something is defined or who calls it.

Each kind of knowledge goes to the mechanism that fits it:

- Checkable behavior: an executable pin, item 13's generated test; breaking it fails the rules.
- A rule or invariant with a place in the code: a text pin anchored to a node, function or module.
- A global decision with no anchor: the repository's own AGENTS.md, not a format of ours, so any
  tool that opens the repository reads it.
- A procedure: the repository's own skills, as they exist; nothing to build.
- Authorizations: [standing grants](#standing-grants).
- Facts an agent discovered by running something, such as a test that needs `TZ=UTC`: later, with
  the command output as evidence, and not in a first version.

Text pins load deterministically, with no model choosing: the pins in the task's zone; the pins in
its cone, here including possible calls, because for context a false positive costs only tokens;
and an index that is always present, one line per pin, capped at about 30 lines, holding only what
search cannot derive.

Muse Spark Open can still only stop or flag. It checks whether a change contradicts a text pin in
its zone or cone; if so, the change waits, quoting the pin. When an anchored node changes, it says
whether the pin still applies: unsure becomes an inbox entry for the person, and it never deletes a
pin. It rejects proposed entries that duplicate or contradict one of the maintainer's decisions. It
never accepts alone and never chooses what is loaded.

Only the person creates or confirms text pins and decisions. An agent may propose one, and it
counts once the person confirms it. Text an agent read in the repository never becomes a pin on its
own. Pins live in Oversight's state with provenance (who confirmed it, from which task) and show in
the graph through item 13's Pinned lens. When the person gives the same answer three times,
Oversight offers to save it as a pin; the offer is deterministic, with no model.

Before committing to build text pins, after the freeze and only if budget remains after proof 2:
about 10 hand-written rules for Boltons and their index go into the task text through saved flows,
with no new code. The same 5 tasks run twice, without and with them, measured by what Oversight
already records: steps to the first edit, search and read calls, cost, tests, and changes that
contradict a rule. Success is fixed before measuring: fewer rule contradictions at equal or lower
cost. If the rules only save a few steps, text pins are not built.

Contract check, 2026-10-29: `ExamplePin` (contracts 1.5.0) is a confirmation stamp inside a task
example, which requires inputs and an expected result, so a text rule cannot be one without faking
those fields. Item 13's pin record carries the kind and anchor instead (item 13 above). Since
2026-10-29, adding a value to an existing enum is a minor change under `docs/proposal.md` section
4.9 when every existing payload still validates and every consumer of the enum, TypeScript and
Python, is exhaustive, so a later text-rule kind can ship in a minor version.

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
20:00 ART; submission target Wed Nov 4 at 20:00 ART. The pitch and the README state what is already
isolated today, test runs in their own network and PID namespaces, and never claim a sandbox.

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
