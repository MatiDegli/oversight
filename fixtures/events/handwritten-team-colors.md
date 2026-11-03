# Team color presentation

Handwritten events over Boltons d31d821, not a recorded run. No model calls or credits.
The capture harness loads the original offline snapshot through exactFixtures.testSupport.ts
and folds its graph.snapshot plus these events with the production reducer. The fixture has
no replacement graph and does not mutate runtime slots.

Event cuts (fixture sequence, before the harness inserts the snapshot after run.started):

- graph-busy: 19.
- chat-flat: 19.
- inbox: 21.
- graph-rest: 37.
- seventh-team: 58.

A and B are two leads with three and two subtasks. C is person-started. D and E later
start coding agents; F is an idle lead without a coding agent. G is the seventh team.
B has the oldest waiting proposal, so Q4 skips it; C is the longest-idle eligible donor.
C and G retain distinct letters while sharing the violet palette family. Resolving A and
C does not fabricate graph deltas: this fixture tests presentation, not code effects.
Proposal summaries and tasks are handwritten illustrations, not claims about executions.
