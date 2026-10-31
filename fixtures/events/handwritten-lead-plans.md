# Three lead plans

Hand-written fixture, not a recorded model run. No API calls or credits.
The graph and saved positions reuse the hand-written inbox/grants fixture.

- Seq 1–2: one run and its graph.
- Seq 3–8: agent-1 revisions 2, 3, 4 request plan-1, plan-2, plan-3 respectively;
  each plan is proposed by that lead. After seq 8 all three inbox entries are open.
- Seq 9: plan-2 starts; only revision 3's entry closes.
- Seq 10: plan-1 is rejected; only revision 2's entry closes.
- Seq 11: lead revision 5 requests no plan; revision 4's entry stays open.

Plan revisions are independent of lead revisions. Entries retain the first opening
event's evidence sequence (4, 6, 8) and plan id; text follows the latest plan detail.
The plan routes use that id.
Tests also cover foreign ownership, new runs, stale updates and legacy logs whose
lead events omit the field or carry only null. The reducer imposes no plan limit.

Each plan has a short task title, a distinct detail explaining the change and its
purpose, and a distinct subtask task. The inbox shows its title and detail once.

No extra agent events are fabricated. `runtime_leads.py` publishes `LeadStatus`
after each coordination tool through `LeadRun.on_step`; `_lead.py` does not emit
`agent.status` or `agent.step`. The recorded steps 1, 2, 3, 4 therefore remain only
in the lead statuses. Seq 1–11 and their increasing timestamps are unchanged.
At seq 8, three plans are proposed, 7 seconds have elapsed, and there are no coding
agent statuses or steps. The current inbox header consequently reads:
"0 agents took 0 steps in 0 min. These 3 need a person."
That header does not count leads; correcting that scope belongs to another front.
