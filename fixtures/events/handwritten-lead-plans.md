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
