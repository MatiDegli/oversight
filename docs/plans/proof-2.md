# Proof 2: plan and reporting rules

> Status: partly built. Frozen manifest/checks merged in `65199d9`/`40a6e8d`, reliability
> producers in `06c34a5`, agent-hours/prices in `95e40e5`/`56b5f18`, lead runtime in `6818b3b`.
> Lead-plan inbox display followed in `bf9fb04`; plan editing/start UI remains absent.
> Checked against main `8b9b589`; this status does not claim a completed proof 2 run.

## Status

The tasks were approved on 2026-10-30 at 09:32 ART; the reporting rules on 2026-10-30 at
10:10 ART. P1-P6 were approved on 2026-10-30 by 11:57 ART in the coordinator's session.
Everything in this plan is fixed before the run. A rule added later is reported as
added after the run started. Scope: [Three proofs](../additions.md#three-proofs) and
[the paired-arm decision](../../DECISIONS.md#2026-10-29--proof-2-measures-correctness-with-hidden-checks-and-a-paired-arm).
The frozen rules are recorded in [the plan decision](../../DECISIONS.md#2026-10-30--proof-2s-tasks-and-reporting-rules-are-fixed-before-the-run).
The reporting rules remain fixed before the run; delivery status is checked against `8b9b589`.
The proof 2 manifest, commands and feature checks landed in `65199d9`/`40a6e8d`. Contracts 1.8.0
reliability events and dependency plans landed in `bb9dc43`, with producers in `06c34a5` and
`efd592f`. Item 10's hours and dated price table are built (`95e40e5`, `56b5f18`). The lead is
built (`6818b3b`), but only requests plans; a person still starts a proposed plan. Rules activation
remains closed, so these deliveries do not by themselves qualify the planned run.

## Repository and tasks

Boltons is pinned at `d31d821d56c8f7756af9c30a864077f2e5855321` (`d31d821`), as checked in
`backend/src/oversight/workspace/source.py`. Its BSD-3-Clause license was checked by reading
[LICENSE at that exact commit](https://github.com/mahmoud/boltons/blob/d31d821d56c8f7756af9c30a864077f2e5855321/LICENSE)
on 2026-10-30, including its three redistribution conditions.

D1: 16 tasks, 12 backed by open issues and four adding tests only. The frozen task inventory,
prompts, and feature APIs and examples are in tools/eval/proof2/tasks.json on main since
`65199d9`, together with check, validate and the seven feature checks. Checked on `1985dfc`:
`tools/eval/proof2/README.md` and `tools/eval/proof2/runner.py` (manifest loading and runner).
The five bug checks, tests-only mutants and D3 runner landed on main in `40a6e8d` (tools/proof2-checks-2).

Issue numbers refer to Boltons' GitHub issues.

| Task | Kind | Area (Boltons module) | Issue | Open upstream PR |
| --- | --- | --- | --- | --- |
| 1 | bug | listutils | #611 | #613 |
| 2 | bug | dictutils | #612 | #614 |
| 3 | bug | socketutils | #675 | #676 |
| 4 | feature | strutils | #299 | none |
| 5 | feature | mathutils | #311 | none |
| 6 | feature | funcutils | #324 | none |
| 7 | bug | listutils | #660 | #662 |
| 8 | bug | dictutils | #659 | #661 |
| 9 | feature | iterutils | #310 | none |
| 10 | tests only | cacheutils tests | none | none |
| 11 | feature | iterutils | #170 | none |
| 12 | tests only | pathutils tests | none | none |
| 13 | feature | iterutils | #95 | #687 |
| 14 | tests only | statsutils tests | none | none |
| 15 | tests only | mathutils tests (after task 5) | none | none |
| 16 | feature | typeutils | #289 | none |

D2: the seven features (4, 5, 6, 9, 11, 13 and 16) use the agreed API and examples frozen in
that manifest.

Freeze all hidden checks before seeing agent output; keep checks outside the product workspace
(`DECISIONS.md`). Agents have no network.

Queue parallel pairs 1-2, 3-4, 5-6, 7-8, 9-10, 11-12 and 13-14. Task 15 runs after task 5;
task 16 runs alone. Queuing all tasks does not bypass these execution constraints.

D3: a tests-only task (10, 12, 14 and 15) is correct when its new tests pass on the base
code, fail on at least two of three seeded mutants written and frozen before the run, and
make no production code changes.

D4: tasks with open upstream PRs stay: 1 (#613), 2 (#614), 3 (#676), 7 (#662), 8 (#661) and
13 (#687). The run stays pinned at d31d821; hidden checks come from the issue, never the PR.
Replace a task only if its issue is resolved before the run.

## How the run goes

The lead is on main (`6818b3b`) and requests plans only; it does not start agents. The person
starts approved plans through the plan routes. Measure L1-L2 only for work actually performed.
The original fallback rule remains: if the hidden checks, item 8, E6 and item 10 are on main
and the lead is not, proof 2
runs anyway. The person queues the 16 tasks at once, those starts count as the person's
decisions, and L1-L2 are reported as not measured.

Report budget pace to the maintainer before starting. Paid calls have a US$25 Meta-budget cap,
separate from the bench's US$20. Include failed and rejected work and the pilots in this cap.
P5, pilots: Two tasks run first as a preflight: 16 (alone, small) and 4 (a feature in another
area). Their results are not counted. The run then queues all 16, those two included. The
pilots' cost counts against the US$25.

One generation yields two arms: without Oversight, each agent's first proposal with passing
tests, merged as it is; with Oversight, what reached main after rules, questions and revisions.
Keep model, tasks and generation identical; judging the paired arm makes no extra model calls.
For each arm report correct changes on main, regressions let through, questions per accepted
change, questions not needed, and cost and wall time per correct change. Keep grants applied
apart from questions. End on the decisions inbox: agents, hours, changes merged by rules,
questions asked, differences tests missed, cost per accepted change, and the person's decisions.

## Definitions fixed before the run

P1, correct change: At a given revision, every hidden check of the task passes, and Boltons'
own test suite passes. A tests-only task is correct by D3.

P2, fixed what was cited: Count only findings at BLOCKING or SHOULD-FIX that have a path and a
line. Map each one to its innermost node with the span index. The finding counts as fixed when:

- the next reviewed revision changes that node; and
- that revision's review does not cite the same node again at BLOCKING or SHOULD-FIX.

A cited finding with no location is counted apart, as not locatable. Hidden checks never decide
"fixed": they decide correctness and regressions. Span lookup is exposed at
`backend/src/oversight/graph/_workspace.py:WorkspaceGraph.node_at`; findings have path and line fields in
`contracts/src/oversight_contracts/review.py:ReviewFinding`.

New regression has two separate counts: a hidden check that passes at the task's base commit
and fails at this revision, and one that passed at the previous reviewed revision and fails at
this one. Run hidden checks on every reviewed revision. Preserve per-check results and revision ids.

P3, questions that were not needed: A question was not needed when two things hold:

- the revision it was asked about already passed every hidden check of its task;
- the person's answer let that revision through unchanged.

A question whose answer led to a revision that fixed a failing hidden check was needed. Every
other question is reported as undetermined.

P4, review cost per change: The cost of all cross-review calls on a change, across its
revisions, by item 10's attribution.

Attribution source: [item 10's amended rules][item10].

P6, measurement definitions: [queuing keeps the execution constraints](#repository-and-tasks);
[per-check results and revision ids kept](#definitions-fixed-before-the-run);
[questions keyed by decision identity](#what-the-report-measures);
[agent-hours from elapsed run intervals](#what-the-report-measures);
[parallelism as the concurrent-agent count over time, wall time apart](#what-the-report-measures).

## What the report measures

Report every result as a count with its n. A percentage appears only for 10 or more cases.

**E1.** When cross-review passes, sends back or stops a change, how often is it right?
Report all four cells of clean/changes_requested by hidden-check pass/fail per change and reviewed
revision; unknown verdicts against both outcomes separately; P4 review cost.
Source: hidden checks and `events.py:ChangeReviewedEvent`;
verdicts: `contracts/src/oversight_contracts/review.py:CrossReview`.

**E2.** Does the next round fix the cited problem without a new regression?
Report rounds, P2 fixes and not-locatable findings, both regression counts, and hidden checks per
round.
Source: E1 reviews, revision diffs, hidden checks, and revision requests' review_seq
(`contracts/src/oversight_contracts/events.py:ChangeResolved`).

**E3.** Among patches with passing tests, how many change behavior nobody asked for, and of what
kind?
Report proof 3's counts and measurable denominator, split by difference kind below.
Source: task specification and `events.py:ChangeFactsRecordedEvent`,
`review.py:ChangeFacts.behavior_diff`.

**E4.** Why does the person get asked? Report questions per accepted change, split by the
three causes below; apply P3 and count grants separately. Source: originating inbox entries,
answers and resolutions, keyed by decision identity, not repeated status messages.

| Decision kind | Cause |
| --- | --- |
| checks_unavailable | Missing evidence |
| review_card | Model judgment |
| risky_area | The person's own policy |
| examples_before_code | The person's own policy |
| lead_plan | The person's own policy |
| follow_main | The person's own policy; also its own parallel-agent cost subcount |

Policy questions are by design, never counted as missing evidence or model judgment.
A conflict or a failed combined test always comes to the person: follow_main is policy by
design. Assign any decision type added before the run its cause in this plan before the run.
The kinds are defined at `contracts/src/oversight_contracts/decisions.py:DecisionRef`.
On main, `backend/src/oversight/agents/answering.py:UNAVAILABLE_PRODUCERS` marks review_card, follow_main,
risky_area, lead_plan and examples_before_code producers unavailable; these rows fix reporting
causes, not runtime availability.

**E5.** What does a correct change cost with Oversight versus merging the first green patch?
Report both arms' cost and wall time per correct change, by role, including failed and rejected
work; report agent-hours and parallelism. This is the headline: overhead paid versus regressions
stopped.
Source: [item 10 attribution][item10], usage.tick roles
(`contracts/src/oversight_contracts/events.py:UsageTick`),
revision and merge timestamps, and hidden checks. Agent-hours sum agent elapsed run intervals;
parallelism reports the concurrent-agent count over time, with wall time measured separately.
Price source: Meta's official pricing page ([item 10's amended rules][item10]). The repository
stores only the extracted table,
its page URL and retrieval date, not a page copy; leak-guard gets an exception for that one file.
The table and `events/list_prices.py:price_usage` are built (`56b5f18`);
`tools/run_cost.py` reports the dated source. Team/period grouping and aggregate UI remain.
Baseten is a lower bound;
unknown costs stay unknown.

**E6.** How reliable is each model as an agent backend? Per model and host, count 429 and
capacity errors per agent-hour, retries, halts after retries, and malformed tool calls.
Source: contracts 1.8.0 model.attempt_failed and agent.step.error_kind (`bb9dc43`),
produced in `06c34a5`; plan-call failed attempts followed in `c78e5ef`.
The reliability envelopes are defined in `contracts/src/oversight_contracts/events.py`
(`ModelAttemptFailedEvent`, `AgentStep`).

**E7.** Does the server's evidence agree when the agent says it is done?
Compare files, tests_run and tests with the server's diff and test run.
A claim omitted in finish is counted apart. Never parse prose with a model; declarations are agent claims, never evidence.
Source: FinishDeclaration (`contracts/src/oversight_contracts/review.py`), facts
(`contracts/src/oversight_contracts/review.py`), Git diffs and tests.executed
(`contracts/src/oversight_contracts/events.py`).

## Proof 3 inside this run

Break E3 down by the behavior diff's difference kind: returns a different value, now raises X,
no longer raises X, raises Y instead of X, or leaves the object in a different state. Those are
the planned outcome groups in `DECISIONS.md`, not a claim all producers are built.
Methods-1 reached main in `2cfe760`, supporting bounded sync methods/properties and receiver
state comparisons. Additional aliases, range/iterator inputs and supplied new-parameter observations
landed in `2122430`; observations of a call the base rejects are separate from equal-input
comparisons. Boltons task eligibility still depends on supported inputs and effect filters.
Report the share over measurable tasks, alongside the count of unmeasurable tasks and reasons;
unavailable is never "no difference". Under 10 cases, report counts only, never a percentage.
If the share is low or the sample too small, tell the scale story without a percentage.
Do not repeat the run to improve the result.

## The harness-pack pair (H1)

The paid H1 pair was declined on 2026-10-30 (DECISIONS.md, “No paid H1 pair”). The free
sensitivity passes on `ad5de55` met the first two conditions (four unavailable questions per
pass and equal counts), but b04 and b19, unasked differences with passing tests, did not flip.
No paid H1 result is claimed; the counting rules remain unchanged.

The frozen gate is `tools/eval/detection/reporting.py:paid_run_gate`; arm configuration is in
`tools/eval/arms.py`. It requires both fake choices, all 20 labelled ids, at most four unavailable
questions in each pass, equal counts, and every green unasked-difference case flipping. See
`tools/eval/detection/RESULTS.md` for the recorded sensitivity results. This later refusal is
separate from the earlier paid detection run.

## The lead (L1-L3)

L1 asks whether the plan holds: count subtasks redone, re-split or in conflict; zone overlaps;
and acceptance at round 0, against tasks the person started. Source: the built lead’s plan and
execution history (`6818b3b`, `runtime_leads.py:LeadService`); plan.updated, queue.updated and
lead.status have contract envelopes at
`contracts/src/oversight_contracts/events.py` (`PlanUpdatedEvent`, `QueueUpdatedEvent`,
`LeadStatusEvent`).
L2 asks what share of spend goes to planning: lead calls against execution calls, by item 10's
attribution. Keep the disjoint agent/review/plan/lead roles
(`contracts/src/oversight_contracts/review.py`).
Attribution source: [item 10's amended rules][item10].
Without the lead, report L1-L2 as not measured, and count the 16 person-queued starts as decisions.
L3, whether the lead cuts the person's decisions, is future work.

## Not measured, and why

- The arm without Oversight does not model an agent that never receives Request changes; it
  reuses the same generation and selects the first green proposal rather than rerunning agents.
- Unmeasurable behavior is not evidence of unchanged behavior; methods-1 is built (`2cfe760`),
  but unsupported signatures/effects remain unavailable.
- L1-L2 are not measured under the fallback; L3 needs a future comparison.
- Pilots are preflight only; their results are excluded and their spend remains in the cap.

[item10]: ../../DECISIONS.md#2026-10-30--item-10-measures-agent-hours-from-observed-intervals-with-dated-list-prices
