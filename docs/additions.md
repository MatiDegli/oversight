# Beyond level 2

## Three proofs

The freeze is organized around three proofs. Judge each front by the proof it serves.

1. **Would you approve it?** Package 9's static challenge is the submission link and opens the
   video. It must work at phone width so a judge can try it on a phone. The maintainer sees a
   phone-width shot before merge.
2. **One person, many agents.** Run for a few hours on a real open-source repository with a
   permissive license, credited in the video. The maintainer approved pinned Boltons and 16 tasks
   on 2026-10-30
   (DECISIONS.md, “Proof 2’s tasks and reporting rules are fixed before the run”). The coordinator
   reports budget pace before starting. End with counts of changes
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

The maintainer set this order on 2026-10-30, ahead of the queue below. Delivery status:

1. Optional-parameter evidence: B1 shared-parameter comparison and B2 literal defaults landed in
   `647d545`, grouped witnesses in `3068b81`, methods in `2cfe760` and object arguments in
   `8a4a2ec`. Additional literal aliases, range/iterator inputs and supplied new-parameter
   observations landed in `2122430` (`changes/_parameter_extensions.py`,
   `_behavior_publish.py:project_extensions`); extension observations are separate from equal-input
   comparisons. Their dedicated review UI remains absent. The remaining measurement is a fake
   evidence check, then maintainer review before
   any paid rerun; unsupported effects remain not probed. H1's paid pair was declined
   (DECISIONS.md, “No paid H1 pair”).
2. Item 10's agent-hours, project active hours and parallelism landed in `95e40e5`
   (`events/activity_fold.py`, `tools/run_hours.py`); dated prices in `56b5f18`
   (`events/list_prices.py`, `tools/run_cost.py`). Team/period grouping and aggregate UI remain;
   lead hours are unavailable because lead statuses do not distinguish work from waiting.
3. Unprobed reasons use fixed allowlisted phrases (`f44dd62`,
   `changes/_behavior_publish.py:NOT_PROBED_REASONS`) and frontend templates
   (`review/evidenceWords.ts`), rather than model prose.
4. The retry card landed in `26d59f1`: `state/applyAgentEvent.ts:applyAgentEvent` folds
   model.attempt_failed into model waits; the card shows retry data and provider halt detail.

This is the working home for everything beyond level 2. The maintainer set this order on
2026-10-28. When capacity opens, take the first unfinished item that can proceed; dates do not
supersede priority. Level 2 closed on Oct 27; its demo remains the fallback. Queue numbers replace
the old idea numbers. Package numbers identify the Oct 27 work packages. Keep the item numbers:
work through 1–11, then the lead agent, saved flows, 12, 13, 17 and 18; take 14–16 only if time
remains. There are three unnumbered rows: the lead agent, saved agent flows and standing grants.

States were checked against the merge base with `origin/main` at `8b9b589`, including code and merge
history. Queued rows
describe what remains, including where part is already on main. A backend or pure core on main
does not mean its routes or UI are wired. The descriptions give code evidence and boundaries.

Contracts 1.8.0 landed in `bb9dc43`: E6's failed model attempts and item 11's dependency plans
(P1–P5). E6's producer landed in `06c34a5`; the waiting/retry card landed in `26d59f1`
(`frontend/src/state/applyAgentEvent.ts:applyAgentEvent`).

**In contracts proposal:** **Yes** means included in the requested Oct 28 proposal, not approved
or already reserved; **No** means no addition is needed; **Pending** means a contract question
needs the maintainer's decision. Existing reservations are noted below. This document does not
amend [the approved proposal](proposal.md) or authorize edits to `contracts/`.

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 1 | Facts and a summary from events (package 3) | On main — summary producer `027ec36`, review rendering `9cdd02a`; facts row/steps `ae9822d` | Yes — summary; report check needs no field |
| 2 | Behavior diff | Backend and review rows on main — `2896b29`; shared inputs `647d545`, methods/properties `2cfe760`, object arguments `8a4a2ec`, unprobed reasons `f44dd62`; graph count chips and sample popovers `f6d7e2a`; supplied new-parameter observations `2122430` (dedicated UI remains) | Yes |
| 3 | Agent harness pack | On main — opt-in pack `42976ee`, runner wiring `b4446cc` | No |
| 4 | Measured impact cone | On main — caller evidence `1c50382`, GET /impact `920e930`, review rows `09865ec`/`d3f64fd`, review rework `cf1e536`; graph caller words `5ba8584` | Yes |
| 5 | Live overlap between agents | Partial — shared edits and change-lifetime reads `1e51e51`, graph overlap `e4cec56`; edit-inside-another-cone review fact remains absent | No |
| 6 | Fails before, passes after (package 4) | On main — base setup/import errors are unknown `b786f6c`; isolated probes `19443e8` | No — conservative classification uses existing fields; exception type declined |
| 7 | Cross-review and automatic Request changes (package 2) | On main — one-round revision `7da6fa9`, independent reviewer selection `045f589`; manual review route still 501 | No |
| 8 | Merge by rules, follow main and Revert (package 1) | Partial — start capability `1824a46`, Revert route `69aaee8`, person preemption `a2aaa6a`, idle follow-main F5a `769d0c7`, protected-path authority `1b1ed44`; activation closed (RULES_ACTIVATION = False); F5b and Revert UI remain | Yes — protected_paths rule in a new policy profile version; merge/Revert shapes reserved |
| 9 | A change card with three states | Partial — producer `3786310`, witnesses `3068b81`, answer guards `2efd586`, projection `f4ab346`, mounted inbox/answers/grants `7c25429`, lead-plan display `bf9fb04`; rules-resolution rendering built, live activation closed; no dedicated assessment/mode/Revert state fold | Yes |
| 10 | Cost and rounds per accepted change | Partial — usage fold `43f3bb8`, archived totals `bf36052`, agent-hours `95e40e5`, dated prices `56b5f18`; team/period grouping and aggregate UI remain | Yes — stored aggregates; event-derived totals need none |
| 11 | One plan, server-computed zones and task queue (package 6) | Partial — standalone queue `c820fbf`, dependency contracts `bb9dc43`, plan service/routes and internal dependency dispatch `efd592f`, metered single-subtask generation `f907e41`; partition core `deb7148` remains unwired; lead-plan inbox display `bf9fb04` built, plan edit/start UI remains | Yes — subtask dependencies and a relaxed plan validator for sequential/single-agent starts |
| — | [Lead agent](#lead-agent) | Runtime on main `6818b3b` — lead routes and metered plan proposals; requests plans only, without starting agents or merging | No — lead routes and `started_by` reserved |
| — | [Saved agent flows](#saved-agent-flows) | Queued — picker absent at `8b9b589`; after the lead, extends package 12 `c1c19c6` | No while filling task text; a distinct role or tool limits need maintainer review |
| 12 | Examples before code | Partial — E1 selector and bounded examples call `1539ab2`; E2 runtime admission, E3 human confirmation and E4 execution remain | Yes |
| 13 | Pinned examples and graph lenses | Queued — pin contract reserved `649ff0d`; no pins or lens control on main `8b9b589` | Yes |
| 17 | Run rule (package 5) | Backend on main — assertion findings `aa4e658`, removed/skipped checks `071844e`; review rendering remains | Yes — run-rule finding kind for removed, skipped, deleted checks |
| 18 | Remaining packages 11–14 | Partial — run controls `d27875a`, HTTP `d085748`, Pause/Resume card `74bb81d`, mounted conversation/composer `bc6c659`; dictation still 501, replay timeline remains; 11 `ee92691` and 12 `c1c19c6` complete | Yes — separate dictation endpoint, initially 501; message/pause/resume reserved; replay/input controls local |
| — | [Standing grants](#standing-grants) | Backend `93a3664` and inbox grant UI `7c25429` on main — run-local possible_callers, module/run scope and revoke; activation closed; broader scopes, protected-path grants and session expiry remain | Contracts 1.7.0 approved and delivered; creation, nullable SaveFile hash, zones and inventory take the next free version after its approval |

### Only if time remains

| # | Item | State and commit or branch | In contracts proposal |
| --- | --- | --- | --- |
| 14 | Node card explained by examples | Queued — caller evidence `1c50382`; node example view absent at `8b9b589` | Yes |
| 15 | Shape guard | Queued — contract reserved `649ff0d`; no structural comparison producer on main `8b9b589` | Yes |
| 16 | Bounded one-hop context for initial tasks | Queued — initial slice absent at `8b9b589`, waits for bench; revision callers/siblings `ee92691` | No |
| 19 | [Opening the person's own repository](../AGENTS.md#opening-my-own-repository) | Decided 2026-10-31, after the cut line; plan first, with the double review | Plan decides |
| 20 | Minimal native Windows port | Decided 2026-10-31, after 19: per-OS layer, mode without isolation | No |

**Not ranked, goes in regardless:** delivery (package 9; static replay `a0a4624`, challenge
`99a57de`, phone practice `0c884f6`/`b7ea554`/`6ce0d19`, recorded evidence `e621acb` on main), graph
polish (8; scale layout `04f0a41`, life layer `8fad4c2`/`ff80c6f`, exact edges `19a94ce`, camera fit
`37c40b9`/`b860ef4` on main, polish continues), and the task bench (10; arms `9ad1ecf`, frozen
patches `724b5c9`, detection arms `75b30f8`, sensitivity gate `cc52a78` and harness pair `ad5de55`,
split measurement `74b5791`, send accounting `cc023c8`, probe-cause reports `507ce31`/`25d6ea3` on
main; the 2026-10-30 paid detection run cost US$0.0513, recorded in
`tools/eval/detection/RESULTS.md`; the later H1 paid pair was declined).

The unchanged-failure guard (D7) landed in `581d2d4` with exact call-edge reach only
(`changes/_repeat_failure.py:repeat_verdict`); per-test executed nodes are not recorded. Behavior
diff for methods is delivered under item 2, superseding its earlier candidate status.

## Undecided candidates

These retain useful ideas from the old inventory without approving a front or changing the freeze.
None of the candidates below is queued. Delivered and retired ideas are recorded separately;
new candidate work waits for the maintainer’s order.

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
- Resume a halted agent in the same conversation and worktree remains a candidate; the live
  Resume command only releases a pending or active pause (`agents/_control.py:RunControl`).
- Budget reminders with steps reserved for `finish`, and a bounded history with a visible handoff.
- Covered older `read_file` results can now become deterministic stubs with
  `OVERSIGHT_HISTORY_PRUNE_TOKENS` explicitly set; bounded history with a visible handoff remains a
  separate candidate.
- Fresh revision context built from facts, distinct from the existing retained-history revision.
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
    since its base (`backend/src/oversight/changes/_merge.py`, `combined_tests_pass`);
    only the visible queue is new.
- Export reviewed revisions/comments as decision pairs; no training during this event.
- A person-approved plan before editing protected areas; item 8's deterministic veto is separate.
- Person-drawn work zones that limit edits, with a scope-request tool, warn mode and zone growth by approval; item 11's server-computed zones and the reserved `within_zone` rule are separate.
- A TypeScript graph adapter; another language/dependency needs a separate scope decision.
- Outside agents reporting through hooks or MCP, after the hackathon's Meta-only product scope.
- Team integrations, evidence attached to pull requests, and exporting any run as a shareable replay.
- [Scale and depth](../DESIGN.md#scale-and-depth): The map view and folder groups are left out of the current level. They come after the Conversation tab, milestone pulses and life layer part 2, built only from a sketch the maintainer approves (maintainer, 2026-10-31).
- A local model in slot B and named editor cursors. The Tauri shell is decided: see Next.
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
- Filesystem isolation for tests and probes (bubblewrap on Linux); today's isolation covers the
  network and the process tree only. No filesystem sandbox during the hackathon.
- Native backend ports for Windows and macOS, as their own fronts. First a per-OS layer in
  `backend/src/oversight/workspace/` (network isolation, process-tree cleanup, file locks, no-follow
  file opens), then a test-isolation design for each OS. A minimal Windows port, the per-OS layer in
  the mode without isolation, is item 20 under "Only if time remains"; Windows isolation and macOS
  come after the hackathon. Until a port has its own isolation, it runs in the existing mode without
  isolation (network open; rules, Revert and added-test probes disabled). Any new dependency follows
  the license list; filelock (Unlicense) needs the maintainer's OK.
- After the hackathon, as a plan first: run the repository's git hooks as evidence, not inside
  commits: in the isolated runner on the proposal, shown on the card as passed or failed, with any
  formatter edits appearing in the diff. Agent commits keep hooks disabled.
- After the hackathon, as a plan first: repositories without Python open without a graph; agents,
  worktrees, diff, review, cross-review, merge and tests still work. Needs a per-repository test
  command with JUnit output and a center view without the graph (sketch first).
- After the hackathon, after the native Windows port: a Tauri shell, starting with one bounded spike
  in its own directory that touches no product code: a Windows window showing the current UI,
  starting the backend inside WSL, and passing a folder chosen with the native dialog (Windows path
  translated to WSL). Dependency licenses are reported first (MIT, Apache-2.0, BSD or ISC only).
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
steps landed in `ae9822d` (`frontend/src/review/ChangeFactsRow.tsx`). Recognized report claims
such as “tests pass” already produce contradictions against the last agent run
(`frontend/src/review/ReviewFacts.tsx`, `backend/src/oversight/changes/_reviewfacts.py`).
Reuse these checks: they do not understand arbitrary prose and need no new field. The optional
summary contract landed in `649ff0d`; the event-derived producer (`027ec36`) and review rendering
(`9cdd02a`, `frontend/src/review/FactsSummary.tsx`) are on main. Do not put report contradictions
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
(`e72fcc7`, `backend/src/oversight/changes/_proposal.py`). Shared-input comparison landed in
`647d545`; review rows (`2896b29`, `review/BehaviorDiff.tsx`) and per-node count chips with
up to eight sample rows (`f6d7e2a`, `graph/NodeChips.tsx`, `graph/nodeChip.ts:selectNodeChips`)
are built. Chips summarize direct behavior evidence, not measured callers, and label varied results.
Supplied new-parameter observations landed in `2122430` (`changes/_parameter_extensions.py`,
`_behavior_publish.py:project_extensions`); these single-run observations do not establish an
equal-input behavior difference and have no dedicated review rendering yet.
Reuse the demo interpreter, asynchronous bounded runner, guarded base
checkout and C3 session isolation
(`19443e8`; `backend/src/oversight/workspace/capability.py`). Never fall back to an open network.
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
(`backend/src/oversight/agents/tools.py`, `backend/src/oversight/agents/_observations.py`).
Runner failure retention is wired too (`b4446cc`, `backend/src/oversight/workspace/suite_guard.py`).
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
(`backend/src/oversight/changes/_proposal.py`). GET /impact serves bounded graph cones
(`920e930`); measured caller rows (`09865ec`, `d3f64fd`, `review/MeasuredCallers.tsx`) and graph
words (`5ba8584`, `graph/elements.ts:graphElements`) are built: differs, same, varied or not measured.
The graph draws differs and varied on review callers; direct behavior chips belong to item 2. Both runs
require session network isolation, never open-network execution. Unsupported or unmatched calls
are not compared; other nodes stay “not measured”. This is neither per-test
coverage nor proof that all callers behave identically.

### 5. Live overlap between agents

Identify same-node edits on the node and both cards. If B has read something A is changing, say so
on B's card and retain observed reads for the whole change. Edits inside another agent's cone
become a fact at review. Never interrupt the person or move focus for overlap. Overlap warnings
name the shared nodes and never block on their own: they warn, and the person decides.

Live overlap is built (`1e51e51`, `e4cec56`): `state/overlap.ts:selectOverlap` returns sharedEdits
and readOfOthers; `state/applyAgentEvent.ts:applyAgentEvent` retains changeReads and
`agents/AgentCard.tsx` renders card facts. The graph uses pending-both and live cones. Review has
no edit-inside-another-agent’s-cone fact yet. No contract addition; replay shares the live reducer.
Search focus is capped, so observed reads are not a complete audit. Once item 18 supplies messages,
notify the later agent as a separate turn; notification in its next tool result remains Next.

### 6. Fails before, passes after

Tell the person whether a new test demonstrates the change or merely failed to import or set up
on base. Keep package 4's case discovery, selected base/head probes and review output (`b7122ef`);
C3 already requires isolated probes (`backend/src/oversight/changes/_probes.py`). Evidence
classification landed in `b786f6c`.

Base import/setup errors now count as unknown with their diagnostic, using existing probe
results/problems (`backend/src/oversight/changes/_beforeafter.py`); they no longer establish
failure on base. The merge-alone evaluator must never use them as evidence of failure on base.
Build this before merge-alone can activate, with no contract change. That prerequisite is met
by `b786f6c`. An exception-type field was declined for the
Oct 28 proposal. Do not rebuild the functioning probes or claim assertion evidence from an error.

### 7. Cross-review and automatic Request changes

An adverse independent review can send one revision back without the person relaying it. This is
on main through `7da6fa9`, following `95e4723`, `9b81d0b` and `13f955c`
(`backend/src/oversight/runtime_review.py`, `backend/src/oversight/changes/_cross_review.py`).
Drop it as a front. Reuse target validation, durable accounting, human preemption and the one-round
limit in item 8. It is opt-in. Since `045f589`, reviewer selection uses `OVERSIGHT_REVIEW_MODEL`,
independent of slots (`backend/src/oversight/review_config.py`), with `muse-spark-open` as the
default (`backend/src/oversight/settings.py`). The reviewer's model must differ from the
author's (`backend/src/oversight/review_config.py`); another call to the author's model is not
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

Main has policy profiles, the P2b-1 scheduler (`9f70622`), reservation (`5dac67c`), merge
transaction (`73b4f65`) and final barriers (`7aec7d5`). Isolated start capability is connected
(`1824a46`, `changes/_mode.py:start_capability`), but `RULES_ACTIVATION = False` keeps rules closed.
GET and PUT /merge-mode, Revert (`69aaee8`) and bounded history (`0e5305a`) are live
(`api/commands.py:add_command_routes`, `api/routes.py`). Person preemption (`a2aaa6a`), idle
follow-main F5a (`769d0c7`) and protected-path authority (`1b1ed44`) are built. Remaining:
activation, queued/in-flight follow-main F5b and Revert UI; the frontend command client has no
Revert method and `applyEvidenceEvent` does not fold revert.resolved. C3 landed in `19443e8`;
rules and probes require it.
Human approval already retests moved bases
(`backend/src/oversight/changes/_merge.py`), and revision can merge main into the retained branch.
Reuse those paths; rules must test the combined result even when base has not moved.
Enable cross-review with the other model before merge-alone can activate; the merge-alone evaluator
must consume its result and require no blocking finding, alongside the deterministic checks.

Manifest/lockfile, CI, test-configuration and security-path edits always wait for the person and
receive cross-review. The maintainer included a named `protected_paths` rule in the Oct 28 contract
proposal, with a new policy profile version: `RuleId` is closed and profiles validate against it
(`contracts/src/oversight_contracts/common.py`). A server-only veto would hide the policy reason.
Contracts 1.5.0 reserved the rule (`649ff0d`); `1b1ed44` makes the nine-rule profile authoritative
with captured protected-path evidence. Revert and
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
one pending decision per change (`eef7daa`). The inbox is mounted in
`app/shell/FocusPanel.tsx`, counted in `app/TopBar.tsx`, and sends answers, grants and
revocations (`7c25429`, `review/DecisionInbox.tsx`). Lead-plan rows landed in `bf9fb04`;
`state/decisionLifecycle.ts:recordPlanEntries` retains each requested plan while proposed and
`DecisionInbox` renders its text. Plan approval/edit/start controls remain absent.

The inbox shipped with item 9; remaining live state wiring keeps the same item numbers, anchors
and queue order. Visual follow-ups still require the maintainer’s shot review.

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

Reuse target-aware notes (`frontend/src/review/CrossReviewNote.tsx`). The constrained decision
client (`15dcff4`), review-card producer (`3786310`), asked-for summary (`abc281d`) and persisted
answer guards (`2efd586`) are on main; grouped witnesses followed in `3068b81`. The live review
card/inbox controls and rules-resolution rendering are built (`state/applyChangeEvent.ts:applyResolution`,
`review/inboxSelectors.ts:selectChangeCard`, `review/ReviewBody.tsx`). Live rules merges still wait
for activation. `state/applyEvidenceEvent.ts:applyEvidenceEvent` has no dedicated state fold for
change.assessed, merge_mode.changed or revert.resolved, but `applyEvent` retains assessment and
Revert events in decisionEvents for selectors. These projections support recorded evidence
without enabling live rules authority. Optional questions and persisted answers
were reserved in `649ff0d`. Yes never converts an unavailable check to passing evidence.

### 10. Cost and rounds per accepted change

**Agent-hours come first:** proof 2's closing line ("this many agents for this many hours") uses them. The
intervals of one agent are unioned, distinct agents add up, and a lead that only waits for its agents adds
nothing. Use observed intervals, never fixed time buckets: each agent's own working intervals come from its
agent.status events, where starting, working and revising count and ready_for_review, halted, done, stopped and
paused do not. A review is one bounded call that starts when the author's run ends after proposing, so its
interval runs from that agent's ready_for_review status to change.reviewed and it counts as its own actor. Also report project active hours (the union
of all intervals) and parallelism (agent-hours divided by project active hours). The lead runtime is built, but
lead.status cannot tell waiting from working; until its contract can, lead hours are unavailable and its
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

**Grouping:** by team, agent, model and role, split by hour or day. A team is the plan's lead when the plan's
`started_by` names one, else the plan, else the agent itself: a plan the person requests is one team, and two plans
of the same lead are one team (the maintainer, 2026-10-31). Periods follow local wall time, so a
daylight-saving change gives a 23- or 25-hour day. Intervals are clipped at period edges; a cost falls in the
period of its usage.tick and is never prorated. Group rows add up only when every group's activity is complete
and their sum matches the global figure; the total always comes from the global figure. A comparison between
periods uses exact values, never rounded display text, and shows a number only when both sides are complete.

Count failed, stopped, rejected and review work too, divide all spend by accepted changes, and show
role/model breakdowns, revision rounds and budget pace. Keep unsuccessful buckets visible. With
no accepted changes the ratio is unavailable.

Writer/reviewer ticks already carry attribution (`contracts/src/oversight_contracts/events.py:UsageTick`,
`backend/src/oversight/runtime_accounting.py:write_tick`). Fold individual costs and resolution events;
never sum cumulative `spent_usd`. Main has the committed usage/rounds fold (`43f3bb8`) and exposes
change/role totals and rounds in archived GET /changes/{id} (`bf36052`,
`backend/src/oversight/changes/_view.py`); GET /agents/{id} and bounded GET /history are wired
too (`bf36052`, `0e5305a`). The frontend still keeps agent totals, not the aggregate usage view
(`frontend/src/state/applyAgentEvent.ts:applyAgentEvent`). Agent-hours (`95e40e5`,
`events/activity_fold.py:fold_activity`, `tools/run_hours.py`) and dated list-price accounting
(`56b5f18`, `events/list_prices.py:price_usage`, `tools/run_cost.py`) are built. Team/period grouping
and aggregate UI remain. E6 emits failed attempts (`06c34a5`); retry-card rendering is built
(`26d59f1`). Live/replay derivation needs no addition;
optional archived totals/rounds were reserved in `649ff0d`. Unknown usage/unpriced Baseten work stays
unknown, never free. The cumulative product Meta ledger is separate from the bench ledger.

### 11. One plan, server-computed zones and task queue

The person reviews one editable plan; independent subtasks can then run in two slots. The server
computes zones, keeping import/call cycles together and cross-zone edges few, and checks that
their impact cones do not overlap. The model writes subtask text only. Dependent work starts after
its prerequisite merges. Use one agent when no independent split exists. Queue dispatch waits until a slot is actually released.

Main validates/expands supplied zones (`backend/src/oversight/changes/_zones.py`).
Reuse that after deterministic cycle contraction; uncertain calls and same-file zones still need
conservative handling. A pure partition-feasibility core landed in `deb7148`, but has no production
completeness producer or caller (`backend/src/oversight/changes/_partition.py`). Plan routes are
live (`backend/src/oversight/api/commands.py`). Queue routes are live, with the standalone
FIFO dispatcher wired (`c820fbf`); plan queueing and dependency dispatch landed in `efd592f`
(`runtime_plans.py:PlanService.dispatch_plan`, `changes/_plans.py:ready`). Lead-plan inbox display
landed in `bf9fb04` (`state/decisionLifecycle.ts:recordPlanEntries`, `review/DecisionInbox.tsx`).
Plan editing/start controls remain; plan.updated is retained in decisionEvents for that projection,
although `applyEvidenceEvent` has no separate plan/queue state fold. Finishing alone
does not free a retained worktree's slot. Contracts 1.8.0 (`bb9dc43`, P1–P5) now allow one
to six subtasks, validated dependencies and sequential/single-agent starts. The runtime service,
rather than the schemas, starts and dispatches plans.
A partition heuristic does not prove independence; speed claims wait for the split bench.

### Lead agent

The lead runtime landed in `6818b3b` (`runtime_leads.py:LeadService`, live POST /leads and
POST /leads/{id}/stop). It reads repository context and requests plans through
`PlanService.create_and_propose`; it does not start agents, approve plans, review or merge.
The person starts a proposed plan through plan routes; the server owns dispatch and zones.
The inbox displays each requested plan (`bf9fb04`); plan edit/start controls and partition
completeness production remain item 11 work. This is narrower than the
earlier ambition to have the lead start agents for proof 2.

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

E1 landed in `1539ab2`: `examples_selector.py:select_examples` and
`agents/examples.py:run_examples_call` supply a selector and one bounded no-edit call as library
code only. E2 runtime admission, E3 human confirmation and E4 execution remain;
`agents/TaskBox.tsx` still starts coding directly. Optional fields were reserved in `649ff0d`. Unconfirmed prose is not
acceptance evidence. This is selective, not an extra call for every task; a confirmed example's
result needs no model judgment.

### 13. Pinned examples and graph lenses

After a choice, offer an ordinary generated repository test retaining input, expected result,
confirmer and originating task. Pins can be amended; breaking one fails the rules. One control
switches Tests, Hubs and Pinned examples; clicking a gap starts a task with its zone.
Use neutral work light and show the maintainer a shot before merging. Text rules remain Next.

Reuse test nodes/caller counts/files/task entry; main has no pins or lens registry
(`frontend/src/graph/elements.ts`, `frontend/src/graph/camera.ts`). Optional pin provenance
was reserved in `649ff0d`; pin production and lenses remain absent at `8b9b589`.
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

Main opens node source/diffs (`frontend/src/app/view.ts`); the palette (`535e6f6`) adds navigation.
Caller inputs/results are now recorded by item 4 (`1c50382`); this node example view remains absent
at `8b9b589`. Static edges carry no argument/result values
(`contracts/src/oversight_contracts/graph.py`). Source excerpts are the first delivery, not
observed executions. Capture excerpts and later recorded example data for replay: recordings need
not contain unchanged source. This is not a general runtime tracer.

### 15. Shape guard

Ask about duplicated logic instead of reuse, new import cycles/module dependencies, and functions
growing past a size threshold. Build a deterministic before/after analyzer over captured AST/source
and graph data. Spans/import edges exist (`backend/src/oversight/graph/_workspace.py`,
`backend/src/oversight/graph/_link.py`), but no shape producer is wired on main.

Optional findings, evidence and completeness are reserved in `649ff0d`; the producer and card
rendering remain absent at `8b9b589`. State dynamic or unsupported cases. Size remains a review
signal under AGENTS.md, not an automatic rejection
gate or learned risk score.

### 16. Bounded one-hop context for initial tasks

Give the agent a fixed-order slice of target signatures, direct callers/callees and module constants,
about 1,500 tokens at most, explicitly listing omissions. Adopt only after the bench shows fewer
steps and tokens with tasks still solved. The server builds this context as text, never a graph tool
for agents, following AGENTS.md and the existing Request changes context. No contract addition is needed.

Revision callers/siblings already work (`ee92691`, `backend/src/oversight/changes/_neighbors.py`,
`backend/src/oversight/changes/_revision.py`). Reuse ordering/uncertainty labels, add callees/constants
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
`649ff0d` (`backend/src/oversight/changes/_facts.py`). Live review rendering remains absent
at `8b9b589`. Absence of failure does not prove passing. Never infer recovery from truncated
failure lists or force broader kinds into the current wire shape.

<a id="18-remaining-small-packages-if-time-remains"></a>

### 18. Remaining small packages

Package 11's revision callers/siblings (`ee92691`) and package 12's Start an agent here (`c1c19c6`,
`frontend/src/agents/StartAgentHere.tsx`) are complete. Keep them for traceability, not new work.

Package 13 is Muse Voice Transcribe task dictation, last day only, never merge approval. Its
separate audio-upload command/response was reserved in `649ff0d` as an endpoint answering 501
until built; message/pause/resume reservations do not cover it. The dictation route still answers
501 (`backend/src/oversight/api/commands.py`).
Package 14 is person-to-agent messages, pause/resume and replay timeline. Message/pause/resume
routes are live (`backend/src/oversight/api/commands.py:add_command_routes`). Pause/Resume is
on the card (`74bb81d`, `agents/PauseControl.tsx`); Conversation is mounted as a focus tab with a
live composer (`bc6c659`, `app/shell/FocusPanel.tsx:ConversationFocus`). Replay consumes ordered
events, but timeline and seeking remain.
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
(`backend/src/oversight/changes/_grants.py`), so closed rules activation still prevents live
creation/use. The frontend folds grant.created/grant.revoked in
`state/applyEvent.ts:applyEvent`; `review/StandingGrants.tsx` and `review/GrantChoice.tsx` supply
the list, creation choice and revoke (`7c25429`). Broader path/agent scopes, session expiry and
protected-path grants remain.

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

Non-Python highlighting shipped on main in `e1e5599` and `b238a6c`: YAML, Markdown, shell and
INI select tokens by extension; TOML uses INI tokens and JSON stays plain text. File view and live
diff use `frontend/src/editor/language.ts:languageForPath`, without language-service workers.
Files producing
no graph nodes have a discreet “No impact analysis” header label. This improves file reading
without claiming graph analysis for another language.

### Delivery, graph polish and bench

**Delivery (package 9).** After item 2 lands, prioritize a static “Would you approve it?” challenge:
three changes, local Approve/Reject choices, findings and score. This is proof 1, the submission
link and the opening of the video. It must work at phone width; show the maintainer a phone-width
shot before merge so judges can try it from a phone. Main has verified backend-free
replay (`a0a4624`), the challenge (`99a57de`) and phone practice (`0c884f6`, `b7ea554`, `6ce0d19`).
`app/challenge/PracticePanel.tsx` scores “Matches with recorded decisions”; this is agreement
with the recording, not a correctness score. Use replay cuts and a
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
`tools/eval/README.md`). Earlier ten or 15–20 task counts are not present coverage. Arms landed
in `9ad1ecf`;
`tools/eval/ledger.py:Ledger.completed` keys attempts by task, profile, arm_id and repetition and
binds the arm configuration hash.
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
