"""Inlined historical source fixtures; tests never invoke git."""

from oversight.changes import detect_shortcuts

OLD = (
    '"""The spending brake: configured or closed, the ledger across restarts,'
    ' warnings, settling."""\n\nimport asyncio\nimport json\nimport os\nfrom pathl'
    "ib import Path\nfrom typing import Any\n\nimport pytest\nfrom live_helpers i"
    "mport (\n    DEADLINE_S,\n    Scripts,\n    is_type,\n    live_server,\n    l"
    "ive_settings,\n    meta_settings,\n    script_json,\n)\n\nfrom oversight.agen"
    "ts import MUSE_SPARK, LedgerFile, LedgerState, UnknownUsage\nfrom oversig"
    "ht.app import ledger_command\nfrom oversight.settings import Settings\nfro"
    "m oversight.workspace import WorkspaceBusy\nfrom oversight_contracts.comm"
    'ands import ErrorResponse\n\npytestmark = pytest.mark.anyio\n\nUSAGE = {"inp'
    'ut_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}\nMETERED = sc'
    'ript_json(\n    "Finish at once.",\n    [{"tool_calls": [{"name": "finish"'
    ', "arguments": {"summary": "Done."}}], "usage": USAGE}],\n)\nNO_USAGE = sc'
    'ript_json(\n    "Finish at once.", [{"tool_calls": [{"name": "finish", "a'
    'rguments": {"summary": "Done."}}]}]\n)\n\n\ndef write_ledger(settings: Setti'
    "ngs, body: object) -> None:\n    settings.runtime_dir.mkdir(parents=True,"
    " exist_ok=True)\n    text = body if isinstance(body, str) else json.dumps"
    '(body)\n    (settings.runtime_dir / "ledger.json").write_text(text)\n\n\ndef'
    " read_ledger(settings: Settings) -> dict[str, Any]:\n    return json.load"
    's((settings.runtime_dir / "ledger.json").read_text())\n\n\nasync def start_'
    "code(settings: Settings, scripts: Scripts) -> tuple[int, str | None]:\n  "
    "  async with live_server(settings, scripts) as s:\n        response = awa"
    'it s.client.post("/agents", json={"task": "t"})\n        if response.stat'
    'us_code == 201:\n            await s.until(is_type("agent.status", state='
    '"ready_for_review"))\n            return 201, None\n        return respons'
    "e.status_code, ErrorResponse.model_validate(response.json()).error.code\n"
    '\n\n@pytest.mark.parametrize(\n    ("overrides", "ledger"),\n    [\n        ('
    '{"budget_usd": None}, None),\n        ({"output_usd_per_m": None}, None),'
    '\n        ({}, "not json"),\n        ({}, {"spent_usd": "12", "unknown": ['
    ']}),\n        ({}, {"spent_usd": 1.0, "unknown": [{"run_id": "r", "agent_'
    'id": "agent-1", "ts": "t"}]}),\n        ({}, {"spent_usd": 135.01, "unkno'
    'wn": []}),\n    ],\n    ids=["no-budget", "no-price", "not-json", "malform'
    'ed", "unknown-entry", "above-90"],\n)\nasync def test_a_closed_brake_refus'
    "es_before_any_client(\n    tmp_path: Path, overrides: dict[str, Any], led"
    "ger: object\n) -> None:\n    settings = meta_settings(tmp_path, **override"
    "s)\n    if ledger is not None:\n        write_ledger(settings, ledger)\n   "
    " scripts = Scripts([METERED], MUSE_SPARK)\n    async with asyncio.timeout"
    "(DEADLINE_S):\n        assert await start_code(settings, scripts) == (403"
    ', "spend_brake")\n    assert scripts.calls == 0\n\n\nasync def test_an_unrea'
    "dable_ledger_is_reported_at_start(tmp_path: Path) -> None:\n    settings "
    '= meta_settings(tmp_path)\n    write_ledger(settings, "not json")\n    asy'
    "nc with asyncio.timeout(DEADLINE_S), live_server(settings, Scripts([], M"
    'USE_SPARK)) as s:\n        errors = [e for e in s.events() if e.type == "'
    'error"]\n        assert [e.payload.source for e in errors] == ["server"]\n'
    '        assert "ledger" in errors[0].payload.message\n    assert (setting'
    's.runtime_dir / "ledger.json").read_text() == "not json"\n\n\n@pytest.mark.'
    'parametrize(\n    ("spent", "brake"), [(75.0, "warn_50"), (120.0, "warn_8'
    '0"), (135.0, "blocked")]\n)\nasync def test_from_half_the_budget_runs_star'
    "t_and_ticks_warn(\n    tmp_path: Path, spent: float, brake: str\n) -> None"
    ":\n    # 135 of 150 is exactly 90%: it starts, and its first call takes i"
    "t past, so the tick says\n    # blocked while the run goes on.\n    settin"
    'gs = meta_settings(tmp_path)\n    write_ledger(settings, {"spent_usd": sp'
    'ent, "unknown": []})\n    async with (\n        asyncio.timeout(DEADLINE_S'
    "),\n        live_server(settings, Scripts([METERED], MUSE_SPARK)) as s,\n "
    '   ):\n        assert (await s.client.post("/agents", json={"task": "t"})'
    ").status_code == 201\n        [tick] = [e for e in await s.until(is_type("
    '"usage.tick")) if e.type == "usage.tick"]\n    # 1000 input and 100 outpu'
    "t tokens at 0.10 and 0.20 per million.\n    cost = (1000 * 0.10 + 100 * 0"
    ".20) / 1_000_000\n    assert tick.payload.brake == brake\n    assert tick."
    "payload.cost_usd == pytest.approx(cost)\n    assert tick.payload.spent_us"
    'd == pytest.approx(spent + cost)\n    assert read_ledger(settings) == {"s'
    'pent_usd": pytest.approx(spent + cost), "unknown": []}\n\n\nasync def test_'
    "replies_without_usage_refuse_the_next_start_until_settled(tmp_path: Path"
    ") -> None:\n    settings = meta_settings(tmp_path)\n    async with asyncio"
    ".timeout(DEADLINE_S):\n        async with live_server(settings, Scripts(["
    "NO_USAGE], MUSE_SPARK)) as s:\n            created = (await s.client.post"
    '("/agents", json={"task": "t"})).json()\n            await s.until(is_typ'
    'e("agent.status", state="ready_for_review"))\n            [entry] = read_'
    'ledger(settings)["unknown"]\n            assert entry["run_id"] == s.live'
    '.run_id and entry["agent_id"] == "agent-1"\n            assert (\n        '
    "        await s.client.post(f\"/changes/{created['change_id']}/reject\")\n "
    '           ).status_code == 202\n            await s.until(is_type("agent'
    '.status", state="done"))\n            refused = await s.client.post("/age'
    'nts", json={"task": "t"})\n            assert ErrorResponse.model_validat'
    'e(refused.json()).error.code == "spend_brake"\n            with pytest.ra'
    "ises(WorkspaceBusy):\n                await asyncio.to_thread(ledger_comm"
    "and, settings, 0.5)\n        # A restart, and its reset, keep the ledger."
    "\n        assert await start_code(settings, Scripts([NO_USAGE], MUSE_SPAR"
    'K)) == (403, "spend_brake")\n        printed = await asyncio.to_thread(le'
    'dger_command, settings, 0.5)\n        assert json.loads(printed) == {"spe'
    'nt_usd": 0.5, "unknown": []}\n        assert await start_code(settings, S'
    "cripts([METERED], MUSE_SPARK)) == (201, None)\n\n\nasync def test_the_fake_"
    "model_is_never_braked(tmp_path: Path) -> None:\n    settings = live_setti"
    'ngs(tmp_path)\n    write_ledger(settings, "not json")\n    async with asyn'
    "cio.timeout(DEADLINE_S):\n        assert await start_code(settings, Scrip"
    'ts([NO_USAGE])) == (201, None)\n    assert (settings.runtime_dir / "ledge'
    'r.json").read_text() == "not json"\n\n\ndef test_a_ledger_write_failing_bef'
    "ore_the_replace_keeps_the_old_file(\n    tmp_path: Path, monkeypatch: pyt"
    'est.MonkeyPatch\n) -> None:\n    ledger = LedgerFile(tmp_path / "ledger.js'
    'on")\n    ledger.write(LedgerState(2.0, ()))\n    before = ledger.path.rea'
    'd_text()\n\n    def broken_fsync(fd: int) -> None:\n        raise OSError("'
    'disk full")\n\n    monkeypatch.setattr(os, "fsync", broken_fsync)\n    entr'
    'y = UnknownUsage("run-20261026-000000", "agent-1", None, "2026-10-26T00:'
    '00:00.000Z")\n    with pytest.raises(OSError, match="disk full"):\n       '
    " ledger.write(LedgerState(3.0, (entry,)))\n    assert ledger.path.read_te"
    "xt() == before\n    assert sorted(p.name for p in tmp_path.iterdir()) == "
    '["ledger.json"]\n    assert ledger.read() == LedgerState(2.0, ())\n\n\ndef t'
    "est_settling_sets_the_spend_and_clears_unknown_calls(tmp_path: Path) -> "
    "None:\n    settings = live_settings(tmp_path)\n    write_ledger(\n        s"
    'ettings,\n        {\n            "spent_usd": 9.0,\n            "unknown": '
    '[{"run_id": "r", "agent_id": "agent-1", "model": None, "ts": "t"}],\n    '
    '    },\n    )\n    assert json.loads(ledger_command(settings, None))["spen'
    't_usd"] == 9.0\n    assert json.loads(ledger_command(settings, 40.0)) == '
    '{"spent_usd": 40.0, "unknown": []}\n'
)

NEW = (
    '"""The spending brake: configured or closed, the ledger across restarts,'
    ' warnings, settling."""\n\nimport asyncio\nimport json\nimport os\nfrom pathl'
    "ib import Path\nfrom typing import Any\n\nimport pytest\nfrom live_helpers i"
    "mport (\n    DEADLINE_S,\n    Scripts,\n    is_type,\n    live_server,\n    l"
    "ive_settings,\n    meta_settings,\n    script_json,\n)\n\nfrom oversight.agen"
    "ts import MUSE_SPARK, LedgerFile, LedgerState, UnknownUsage\nfrom oversig"
    "ht.app import ledger_command\nfrom oversight.settings import Settings\nfro"
    "m oversight.workspace import WorkspaceBusy\nfrom oversight_contracts.comm"
    'ands import ErrorResponse\n\npytestmark = pytest.mark.anyio\n\nUSAGE = {"inp'
    'ut_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}\nMETERED = sc'
    'ript_json(\n    "Finish at once.",\n    [{"tool_calls": [{"name": "finish"'
    ', "arguments": {"summary": "Done."}}], "usage": USAGE}],\n)\nNO_USAGE = sc'
    'ript_json(\n    "Finish at once.", [{"tool_calls": [{"name": "finish", "a'
    'rguments": {"summary": "Done."}}]}]\n)\n\n\ndef write_ledger(settings: Setti'
    "ngs, body: object) -> None:\n    settings.runtime_dir.mkdir(parents=True,"
    " exist_ok=True)\n    text = body if isinstance(body, str) else json.dumps"
    '(body)\n    (settings.runtime_dir / "ledger.json").write_text(text)\n\n\ndef'
    " read_ledger(settings: Settings) -> dict[str, Any]:\n    return json.load"
    's((settings.runtime_dir / "ledger.json").read_text())\n\n\nasync def start_'
    "code(settings: Settings, scripts: Scripts) -> tuple[int, str | None]:\n  "
    "  async with live_server(settings, scripts) as s:\n        response = awa"
    'it s.client.post("/agents", json={"task": "t"})\n        if response.stat'
    'us_code == 201:\n            await s.until(is_type("agent.status", state='
    '"ready_for_review"))\n            return 201, None\n        return respons'
    "e.status_code, ErrorResponse.model_validate(response.json()).error.code\n"
    '\n\n@pytest.mark.parametrize(\n    ("overrides", "ledger"),\n    [\n        ('
    '{"budget_usd": None}, None),\n        ({"output_usd_per_m": None}, None),'
    '\n        ({}, "not json"),\n        ({}, {"spent_usd": "12", "unknown": ['
    ']}),\n        ({}, {"spent_usd": 1.0, "unknown": [{"run_id": "r", "agent_'
    'id": "agent-1", "ts": "t"}]}),\n        ({}, {"spent_usd": 135.01, "unkno'
    'wn": []}),\n    ],\n    ids=["no-budget", "no-price", "not-json", "malform'
    'ed", "unknown-entry", "above-90"],\n)\nasync def test_a_closed_brake_refus'
    "es_before_any_client(\n    tmp_path: Path, overrides: dict[str, Any], led"
    "ger: object\n) -> None:\n    settings = meta_settings(tmp_path, **override"
    "s)\n    if ledger is not None:\n        write_ledger(settings, ledger)\n   "
    " scripts = Scripts([METERED], MUSE_SPARK)\n    async with asyncio.timeout"
    "(DEADLINE_S):\n        assert await start_code(settings, scripts) == (403"
    ', "spend_brake")\n    assert scripts.calls == 0\n\n\nasync def test_an_unrea'
    "dable_ledger_is_reported_at_start(tmp_path: Path) -> None:\n    settings "
    '= meta_settings(tmp_path)\n    write_ledger(settings, "not json")\n    asy'
    "nc with asyncio.timeout(DEADLINE_S), live_server(settings, Scripts([], M"
    'USE_SPARK)) as s:\n        errors = [e for e in s.events() if e.type == "'
    'error"]\n        assert [e.payload.source for e in errors] == ["server"]\n'
    '        assert "ledger" in errors[0].payload.message\n    assert (setting'
    's.runtime_dir / "ledger.json").read_text() == "not json"\n\n\n@pytest.mark.'
    'parametrize(\n    ("spent", "brake"), [(75.0, "warn_50"), (120.0, "warn_8'
    '0"), (135.0, "blocked")]\n)\nasync def test_from_half_the_budget_runs_star'
    "t_and_ticks_warn(\n    tmp_path: Path, spent: float, brake: str\n) -> None"
    ":\n    # 135 of 150 is exactly 90%: it starts, and its first call takes i"
    "t past, so the tick says\n    # blocked while the run goes on.\n    settin"
    'gs = meta_settings(tmp_path)\n    write_ledger(settings, {"spent_usd": sp'
    'ent, "unknown": []})\n    async with (\n        asyncio.timeout(DEADLINE_S'
    "),\n        live_server(settings, Scripts([METERED], MUSE_SPARK)) as s,\n "
    '   ):\n        assert (await s.client.post("/agents", json={"task": "t"})'
    ").status_code == 201\n        [tick] = [e for e in await s.until(is_type("
    '"usage.tick")) if e.type == "usage.tick"]\n    # 1000 input and 100 outpu'
    "t tokens at 0.10 and 0.20 per million.\n    cost = (1000 * 0.10 + 100 * 0"
    ".20) / 1_000_000\n    assert tick.payload.brake == brake\n    assert tick."
    "payload.cost_usd == pytest.approx(cost, rel=0, abs=1e-12)\n    assert tic"
    "k.payload.spent_usd == pytest.approx(spent + cost, rel=0, abs=1e-9)\n    "
    'assert read_ledger(settings) == {\n        "spent_usd": pytest.approx(spe'
    'nt + cost, rel=0, abs=1e-9),\n        "unknown": [],\n    }\n\n\nasync def te'
    "st_replies_without_usage_refuse_the_next_start_until_settled(tmp_path: P"
    "ath) -> None:\n    settings = meta_settings(tmp_path)\n    async with asyn"
    "cio.timeout(DEADLINE_S):\n        async with live_server(settings, Script"
    "s([NO_USAGE], MUSE_SPARK)) as s:\n            created = (await s.client.p"
    'ost("/agents", json={"task": "t"})).json()\n            await s.until(is_'
    'type("agent.status", state="ready_for_review"))\n            [entry] = re'
    'ad_ledger(settings)["unknown"]\n            assert entry["run_id"] == s.l'
    'ive.run_id and entry["agent_id"] == "agent-1"\n            assert (\n     '
    "           await s.client.post(f\"/changes/{created['change_id']}/reject\""
    ')\n            ).status_code == 202\n            await s.until(is_type("ag'
    'ent.status", state="done"))\n            refused = await s.client.post("/'
    'agents", json={"task": "t"})\n            assert ErrorResponse.model_vali'
    'date(refused.json()).error.code == "spend_brake"\n            with pytest'
    ".raises(WorkspaceBusy):\n                await asyncio.to_thread(ledger_c"
    "ommand, settings, 0.5)\n        # A restart, and its reset, keep the ledg"
    "er.\n        assert await start_code(settings, Scripts([NO_USAGE], MUSE_S"
    'PARK)) == (403, "spend_brake")\n        printed = await asyncio.to_thread'
    '(ledger_command, settings, 0.5)\n        assert json.loads(printed) == {"'
    'spent_usd": 0.5, "unknown": []}\n        assert await start_code(settings'
    ", Scripts([METERED], MUSE_SPARK)) == (201, None)\n\n\nasync def test_the_fa"
    "ke_model_is_never_braked(tmp_path: Path) -> None:\n    settings = live_se"
    'ttings(tmp_path)\n    write_ledger(settings, "not json")\n    async with a'
    "syncio.timeout(DEADLINE_S):\n        assert await start_code(settings, Sc"
    'ripts([NO_USAGE])) == (201, None)\n    assert (settings.runtime_dir / "le'
    'dger.json").read_text() == "not json"\n\n\ndef test_a_ledger_write_failing_'
    "before_the_replace_keeps_the_old_file(\n    tmp_path: Path, monkeypatch: "
    'pytest.MonkeyPatch\n) -> None:\n    ledger = LedgerFile(tmp_path / "ledger'
    '.json")\n    ledger.write(LedgerState(2.0, ()))\n    before = ledger.path.'
    "read_text()\n\n    def broken_fsync(fd: int) -> None:\n        raise OSErro"
    'r("disk full")\n\n    monkeypatch.setattr(os, "fsync", broken_fsync)\n    e'
    'ntry = UnknownUsage("run-20261026-000000", "agent-1", None, "2026-10-26T'
    '00:00:00.000Z")\n    with pytest.raises(OSError, match="disk full"):\n    '
    "    ledger.write(LedgerState(3.0, (entry,)))\n    assert ledger.path.read"
    "_text() == before\n    assert sorted(p.name for p in tmp_path.iterdir()) "
    '== ["ledger.json"]\n    assert ledger.read() == LedgerState(2.0, ())\n\n\nde'
    "f test_settling_sets_the_spend_and_clears_unknown_calls(tmp_path: Path) "
    "-> None:\n    settings = live_settings(tmp_path)\n    write_ledger(\n      "
    '  settings,\n        {\n            "spent_usd": 9.0,\n            "unknown'
    '": [{"run_id": "r", "agent_id": "agent-1", "model": None, "ts": "t"}],\n '
    '       },\n    )\n    assert json.loads(ledger_command(settings, None))["s'
    'pent_usd"] == 9.0\n    assert json.loads(ledger_command(settings, 40.0)) '
    '== {"spent_usd": 40.0, "unknown": []}\n'
)


def test_commit_4b2ebac_tightening_is_clean() -> None:
    assert detect_shortcuts([("backend/tests/api/test_brake.py", OLD, NEW)]) == []
