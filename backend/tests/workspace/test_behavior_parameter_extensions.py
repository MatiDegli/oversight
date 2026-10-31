"""Binding rejections are observations only for the opt-in supplied-call lane."""

import json
import sys
from dataclasses import asdict

import pytest

from oversight.workspace import BehaviorTarget, run_process
from oversight.workspace.behavior_run import WORKER


async def invoke_extension(root, body, target):
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text(body)
    request, report = root / "request.json", root / "report.json"
    request.write_text(json.dumps([asdict(target)]))
    result = await run_process(
        [sys.executable, "-B", str(WORKER), str(root), str(request), str(report), "1.0"],
        cwd=root,
        timeout_s=30,
        env={"HOME": str(root), "PYTHONHASHSEED": "0"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    return json.loads(report.read_text())[target.id]


@pytest.mark.anyio
@pytest.mark.parametrize("observe_binding", [False, True])
async def test_only_opted_in_binding_rejection_is_a_typed_exception(tmp_path, observe_binding):
    target = BehaviorTarget(
        "sample:f", "sample", "f", ("((), {'extra': 3})",), observe_binding=observe_binding
    )
    result = await invoke_extension(tmp_path, "def f(): return 1\n", target)
    [row] = result["results"]
    assert row["kind"] == ("raised" if observe_binding else "unrepresentable")
    assert bool(row.get("binding_rejected")) == observe_binding
    if observe_binding:
        assert row["class"] == "builtins.TypeError"
        assert "extra" in row["message"] and row["fingerprint"]


@pytest.mark.anyio
@pytest.mark.parametrize("body_error", [False, True])
async def test_kwargs_acceptance_and_body_typeerror_are_not_binding_rejections(
    tmp_path, body_error
):
    body = "def f(**kwargs):\n    " + (
        "raise TypeError('body')\n" if body_error else "return kwargs['extra']\n"
    )
    target = BehaviorTarget(
        "sample:f", "sample", "f", ("((), {'extra': 3})",), observe_binding=True
    )
    [row] = (await invoke_extension(tmp_path, body, target))["results"]
    assert row["kind"] == ("raised" if body_error else "value")
    assert "binding_rejected" not in row


@pytest.mark.anyio
async def test_method_binding_rejection_keeps_constructor_setup_out_of_rejection_count(tmp_path):
    body = "class Box:\n    def __init__(self, x): self.x = x\n    def f(self): return self.x\n"
    target = BehaviorTarget(
        "sample:Box.f",
        "sample",
        "f",
        (repr(("((2,), {})", "((), {'extra': 3})")), repr(("((), {})", "((), {'extra': 3})"))),
        receiver_class="Box",
        method_kind="method",
        observe_binding=True,
    )
    rejected, setup = (await invoke_extension(tmp_path, body, target))["results"]
    assert rejected["kind"] == "raised" and rejected["binding_rejected"] is True
    assert setup["kind"] == "unrepresentable" and "binding_rejected" not in setup


@pytest.mark.anyio
@pytest.mark.parametrize("observe_binding", [False, True])
async def test_extension_invocation_preserves_positional_and_keyword_spelling(
    tmp_path, observe_binding
):
    body = (
        "from functools import wraps\n"
        "def observe(func):\n"
        "    @wraps(func)\n"
        "    def wrapped(*args, **kwargs): return (len(args), len(kwargs))\n"
        "    return wrapped\n"
        "@observe\n"
        "def f(extra=0): return extra\n"
    )
    target = BehaviorTarget(
        "sample:f", "sample", "f", ("((), {'extra': 3})",), observe_binding=observe_binding
    )
    [row] = (await invoke_extension(tmp_path, body, target))["results"]
    assert row["value"][1] == (
        [["int", "0x0"], ["int", "0x1"]] if observe_binding else [["int", "0x1"], ["int", "0x0"]]
    )
