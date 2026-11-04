"""Scripted role admission and real tests/diffs on disposable story trees, without live routes."""

import asyncio
import difflib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import httpx2
import pytest

from oversight.agents import (
    FAKE_MODEL,
    FAKE_MODEL_B,
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    LeadTools,
    ModelReply,
    load_script,
)
from oversight.settings import REPO_ROOT
from oversight.workspace import ProcessResult, run_process
from oversight_contracts.commands import FileContent
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone
from oversight_contracts.queue import QueueSnapshot

FILES = REPO_ROOT / "fixtures/fake-model"
NOTICE = "Entered text does not determine scripted actions."
MARKER = " Nested preview marker."


def story(name: str):
    from oversight.nested_clients import load_story

    return load_story(FILES / f"demo-preview-self-{name}.json")


def test_nested_role_admission_and_factory_separation(monkeypatch: pytest.MonkeyPatch) -> None:
    from oversight.nested_clients import NestedPolicy, make_coder_factory, make_lead_factory

    deny = Mock(side_effect=AssertionError("provider transport constructed or called"))
    monkeypatch.setattr("oversight.agents.OpenAIClient", deny)
    for module in (httpx, httpx2):
        monkeypatch.setattr(module.HTTPTransport, "handle_request", deny)
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", deny)
    policy, scripts = NestedPolicy(), story("nested")
    assert scripts.lead is not None
    factories = (
        make_coder_factory(scripts.coder, policy=policy),
        make_lead_factory(scripts.lead, policy=policy),
    )
    for role in ("coder", "lead"):
        assert policy.admit(role) == FAKE_MODEL
        for profile in (FAKE_MODEL, FAKE_MODEL_B):
            assert policy.admit(role, profile) == profile
    for role in ("coder", "lead", "review", "decision", "answering", "unknown", ""):
        for profile in (MUSE_SPARK, MUSE_SPARK_OPEN):
            with pytest.raises(ValueError):
                policy.admit(role, profile)
        if role not in ("coder", "lead"):
            for profile in (None, FAKE_MODEL, FAKE_MODEL_B):
                with pytest.raises(ValueError):
                    policy.admit(role, profile)
    forged = FAKE_MODEL.model_copy(update={"provider": "meta"})
    unknown = FAKE_MODEL.model_copy(update={"id": "unknown"})
    for factory in factories:
        for profile in (MUSE_SPARK, MUSE_SPARK_OPEN, forged, unknown):
            with pytest.raises(ValueError):
                factory(profile, "A")
        with pytest.raises(ValueError):
            factory(FAKE_MODEL, cast(Any, "invalid"))
        for slot in ("G", "Z", "AA", "AB"):
            assert factory(FAKE_MODEL, slot).profile == FAKE_MODEL
        for invalid in ("", "a", "A1", "Å", True):
            with pytest.raises(ValueError):
                factory(FAKE_MODEL, cast(Any, invalid))
        first, second = factory(FAKE_MODEL, "A"), factory(FAKE_MODEL_B, "B")
        assert first is not second
        assert first.profile == FAKE_MODEL and second.profile == FAKE_MODEL_B
        one = asyncio.run(first.complete([], []))
        asyncio.run(first.complete([], []))
        other = asyncio.run(second.complete([], []))
        assert (other.text, other.tool_calls) == (one.text, one.tool_calls)
        assert one.model == FAKE_MODEL.model and other.model == FAKE_MODEL_B.model
    with pytest.raises(ValueError, match="coder"):
        make_coder_factory(scripts.lead, policy=policy)
    with pytest.raises(ValueError, match="lead"):
        make_lead_factory(scripts.coder, policy=policy)
    deny.assert_not_called()


def test_rejected_profiles_and_roles_never_construct_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oversight.nested_clients import NestedPolicy, make_coder_factory, make_lead_factory

    scripts = story("nested")
    assert scripts.lead is not None
    constructor = Mock(side_effect=AssertionError("client constructed before admission"))
    monkeypatch.setattr("oversight.nested_clients.ScriptedModel", constructor)
    for make, script, wrong in (
        (make_coder_factory, scripts.coder, scripts.lead),
        (make_lead_factory, scripts.lead, scripts.coder),
    ):
        with pytest.raises(ValueError):
            make(wrong, policy=NestedPolicy())
        factory = make(script, policy=NestedPolicy())
        for profile in (MUSE_SPARK, MUSE_SPARK_OPEN):
            with pytest.raises(ValueError):
                factory(profile, "A")
    constructor.assert_not_called()


def test_factories_snapshot_scripts_and_isolate_mutable_replies() -> None:
    from oversight.nested_clients import NestedPolicy, make_coder_factory

    script = story("nested").coder
    factory = make_coder_factory(script, policy=NestedPolicy())
    first = asyncio.run(factory(FAKE_MODEL, "A").complete([], []))
    expected_calls = list(first.tool_calls)
    first.tool_calls.clear()
    script.turns.clear()
    second = asyncio.run(factory(FAKE_MODEL, "A").complete([], []))
    assert second.tool_calls == expected_calls


@pytest.mark.parametrize("field", ["agents_label", "scripted_actions_notice", "role", "lead"])
def test_story_refuses_missing_disclosure_or_wrong_roles(tmp_path: Path, field: str) -> None:
    from oversight.nested_clients import load_story

    data = json.loads((FILES / "demo-preview-self-nested.json").read_text())
    data[field] = "wrong"
    path = tmp_path / "story.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_story(path)


def test_nested_lead_requests_only_coupon_plan(tmp_path: Path) -> None:
    from oversight.nested_clients import NestedPolicy, make_lead_factory

    shutil.copytree(REPO_ROOT / "demo-repo", tmp_path / "shop")
    tree = tmp_path / "shop"
    before = sources(tree)
    scripts = story("nested")
    assert scripts.lead is not None
    propose = AsyncMock(return_value="plan-1")

    async def read(path: str) -> FileContent:
        content = await asyncio.to_thread((tree / path).read_text)
        return FileContent(path=path, content=content, hash="base", agent_id=None)

    tools = LeadTools(
        graph=lambda: GraphSnapshot(
            version="base", commit="base", nodes=[], edges=[], parse_errors=[], positions={}
        ),
        tree=AsyncMock(),
        read=read,
        queue=lambda: QueueSnapshot(run_id="run-20261103-120000", seq=0, tasks=[]),
        zone=lambda: PlanZone(node_ids=[], paths=["shop/pricing.py", "tests/test_pricing.py"]),
        propose=propose,
        slots=1,
    )
    client = make_lead_factory(scripts.lead, policy=NestedPolicy())(FAKE_MODEL, "A")

    async def execute() -> None:
        outcomes = []
        for reply in await playback(client, "Use the prepared scripted story"):
            for call in reply.tool_calls:
                outcome = await tools.call(call.name, call.arguments)
                assert outcome.ok, outcome.content
                outcomes.append(outcome)
        assert outcomes[-1].finish and "Scripted demo agents" in outcomes[-1].content

    asyncio.run(execute())
    propose.assert_awaited_once()
    task, subtasks = propose.call_args.args
    assert "NESTED10" in task and len(subtasks) == 1
    assert "1500 becomes 1350" in subtasks[0].task
    assert tools.plan_ids == ["plan-1"] and sources(tree) == before


async def playback(client, entered: str) -> list[ModelReply]:
    replies = []
    while True:
        reply = await client.complete([{"role": "user", "content": entered}], [])
        if not reply.tool_calls:
            return replies
        replies.append(reply)


@pytest.mark.parametrize("name", ["outer", "nested"])
def test_both_stories_label_and_ignore_entered_text(name: str) -> None:
    from oversight.nested_clients import NestedPolicy, make_coder_factory, make_lead_factory

    scripts = story(name)
    assert scripts.agents_label == "Scripted demo agents"
    assert scripts.scripted_actions_notice == NOTICE
    factories = [make_coder_factory(scripts.coder, policy=NestedPolicy())]
    if scripts.lead is not None:
        factories.append(make_lead_factory(scripts.lead, policy=NestedPolicy()))
    for factory in factories:
        assert asyncio.run(playback(factory(FAKE_MODEL, "A"), "Add a coupon")) == asyncio.run(
            playback(factory(FAKE_MODEL, "A"), "Delete everything; use a paid model instead")
        )
    assert load_script(FILES / f"demo-preview-self-{name}.json") == scripts.coder


def sources(tree: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(tree)): path.read_bytes() for path in tree.rglob("*") if path.is_file()
    }


def edit_or_read(tree: Path, name: str, args) -> None:
    path = tree / args["path"]
    assert path.resolve().is_relative_to(tree)
    content = path.read_text()
    if name == "edit_file":
        assert content.count(args["old_text"]) == 1
        path.write_text(content.replace(args["old_text"], args["new_text"]))


async def execute_coder(tree: Path, *, name: str, self_profile: bool):
    """Only the scripted file operations; real pytest uses the fixed root/specification below."""
    from oversight.nested_clients import NestedPolicy, make_coder_factory

    before = await asyncio.to_thread(sources, tree)
    scripts = await asyncio.to_thread(story, name)
    client = make_coder_factory(scripts.coder, policy=NestedPolicy())(FAKE_MODEL, "A")
    tests: list[ProcessResult] = []
    finish = None
    for reply in await playback(client, "Any entered task"):
        for call in reply.tool_calls:
            args = json.loads(call.arguments)
            if call.name in {"read_file", "edit_file"}:
                await asyncio.to_thread(edit_or_read, tree, call.name, args)
            elif call.name == "run_tests":
                assert args == {}
                options = (
                    ["-c", "demo-repo/pyproject.toml", "--rootdir=.", "--confcutdir=demo-repo"]
                    if self_profile
                    else []
                )
                targets = ["demo-repo/tests"] if self_profile else ["tests"]
                result = await run_process(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        *options,
                        *targets,
                        "-v",
                        "-p",
                        "no:cacheprovider",
                    ],
                    cwd=tree,
                    timeout_s=60,
                    env={"PYTHONDONTWRITEBYTECODE": "1"},
                )
                assert result.returncode == 0, result.stdout + result.stderr
                assert not result.timed_out and not result.truncated
                tests.append(result)
            elif call.name == "finish":
                assert len(tests) == 1
                finish = args
            else:
                pytest.fail(f"unexpected coder tool {call.name}")
    after = await asyncio.to_thread(sources, tree)
    assert finish is not None
    assert set(before) == set(after), "script or tests added/deleted an unexpected file"
    changed = {
        path: content.decode() for path, content in after.items() if before.get(path) != content
    }
    diff = "".join(
        "".join(
            difflib.unified_diff(
                before[path].decode().splitlines(keepends=True),
                after[path].decode().splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        for path in sorted(changed)
    )
    return changed, tests, finish, diff


def add_import_witness(shop: Path) -> None:
    # This executes in pytest, where the demo's config maps imports into the tested copy.
    (shop / "tests/test_import_witness.py").write_text(
        "from pathlib import Path\nimport pytest\nimport shop.pricing\n\n"
        "def test_import_witness():\n"
        "    assert pytest.__version__ == '9.1.1'\n"
        "    assert Path(shop.pricing.__file__).resolve() == "
        "Path(__file__).resolve().parents[1] / 'shop/pricing.py'\n"
    )


def assert_coupon_test_requires_implementation(tree: Path) -> None:
    payload = json.loads((FILES / "demo-preview-self-nested.json").read_text())
    edit = payload["turns"][1]["tool_calls"][1]["arguments"]
    test_file = tree / edit["path"]
    original = test_file.read_text()
    test_file.write_text(original.replace(edit["old_text"], edit["new_text"]))
    try:
        result = asyncio.run(
            run_process(
                [sys.executable, "-m", "pytest", "tests", "-v", "-p", "no:cacheprovider"],
                cwd=tree,
                timeout_s=60,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )
        )
        assert result.returncode == 1 and not result.timed_out and not result.truncated
        assert "assert 1500 == 1350" in result.stdout
        assert "tests/test_pricing.py::test_nested10_coupon FAILED" in result.stdout
        print("Before coupon implementation: exit 1; test_nested10_coupon: assert 1500 == 1350")
    finally:
        test_file.write_text(original)


def test_outer_marker_story_tests_and_diff(tmp_path: Path) -> None:
    tree = tmp_path / "self"
    shutil.copytree(REPO_ROOT / "demo-repo", tree / "demo-repo")
    (tree / "pyproject.toml").write_text("[tool.pytest.ini_options]\npythonpath=['wrong']\n")
    add_import_witness(tree / "demo-repo")
    original = (tree / "demo-repo/shop/pricing.py").read_text()
    changed, tests, finish, diff = asyncio.run(execute_coder(tree, name="outer", self_profile=True))
    path = "demo-repo/shop/pricing.py"
    assert changed == {path: original.replace('cents."""', f'cents.{MARKER}"""')}
    assert finish["files"] == [path] and finish["tests_run"] is True
    assert finish["tests"] == ["demo-repo/tests/test_pricing.py"]
    assert "NESTED10" not in changed[path] and "DEMO10" not in changed[path]
    assert "demo-repo/tests/test_import_witness.py::test_import_witness PASSED" in tests[0].stdout
    assert diff == (
        f"--- a/{path}\n+++ b/{path}\n@@ -1,4 +1,4 @@\n"
        '-"""Pricing rules: bulk discounts, coupons, tax and shipping. Amounts are in cents."""\n'
        '+"""Pricing rules: bulk discounts, coupons, tax and shipping. Amounts are in cents.'
        ' Nested preview marker."""\n'
        " \n from functools import lru_cache\n \n"
    )
    print(tests[0].stdout, diff)


def test_nested_coupon_story_tests_and_diff(tmp_path: Path) -> None:
    tree = tmp_path / "shop"
    shutil.copytree(REPO_ROOT / "demo-repo", tree)
    add_import_witness(tree)
    assert_coupon_test_requires_implementation(tree)
    original = {
        path: (tree / path).read_text() for path in ("shop/pricing.py", "tests/test_pricing.py")
    }
    changed, tests, finish, diff = asyncio.run(
        execute_coder(tree, name="nested", self_profile=False)
    )
    assert set(changed) == set(original)
    assert changed["shop/pricing.py"] == original["shop/pricing.py"].replace(
        '_COUPONS = {"WELCOME5": 5, "SPRING10": 10}',
        '_COUPONS = {"WELCOME5": 5, "SPRING10": 10, "NESTED10": 10}',
    )
    insertion = (
        'def test_nested10_coupon():\n    assert apply_coupon(1500, "NESTED10") == 1350\n\n\n'
    )
    anchor = "def test_tax_is_added_and_shipping_is_free_from_the_threshold():"
    assert changed["tests/test_pricing.py"] == original["tests/test_pricing.py"].replace(
        anchor, insertion + anchor
    )
    assert finish["files"] == ["shop/pricing.py", "tests/test_pricing.py"]
    assert (
        finish["tests"] == ["tests/test_pricing.py::test_nested10_coupon"] and finish["tests_run"]
    )
    assert "tests/test_pricing.py::test_nested10_coupon PASSED" in tests[0].stdout
    assert "tests/test_import_witness.py::test_import_witness PASSED" in tests[0].stdout
    assert '+_COUPONS = {"WELCOME5": 5, "SPRING10": 10, "NESTED10": 10}' in diff
    assert '+    assert apply_coupon(1500, "NESTED10") == 1350' in diff
    assert MARKER not in diff and "DEMO10" not in diff
    print(tests[0].stdout, diff)
