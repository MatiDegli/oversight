# Hand-written lead wake attribution fixture

`handwritten-lead-wakes.jsonl` contains hand-written catalog scenarios, not a recording of
runtime-generated plan or coding-agent transitions. The companion
`handwritten-lead-wakes.expected.json` lists the expected lead and trigger seq pairs for Python
and a future frontend attribution test. No TypeScript parity is claimed.

The `_record` helper in `backend/tests/events/test_lead_wake_parity.py` starts a fake-only lead,
injects hand-written payloads through `store.publish`, then uses `export_run` to export the
committed envelopes unchanged. It also writes the expected pairs while publishing. The cases
include a lead id without a started lead, abbreviated plan transitions, and an agent reassigned
between plans to prove event-time ownership; they do not demonstrate runtime lifecycle behavior.

To regenerate both files using scripted fake models:

```sh
OVERSIGHT_RECORD_WAKE_FIXTURE=1 uv run --frozen pytest -n 2 backend/tests/events/test_lead_wake_parity.py::test_handwritten_wake_fixture_matches_predicate_and_watcher
```

Normal tests replay the checked-in fixture without regenerating it or calling a provider.
