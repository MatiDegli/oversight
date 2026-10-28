"""Regression coverage for price isolation, incomplete reports and socket ownership."""

import asyncio
import errno
import os
import socket

import httpx
import pytest
from tools.eval import server
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger
from tools.eval.report import report
from tools.eval.session import allowance
from tools.eval.tasks import BenchError
from tools.eval.tests.test_arms import OFF
from tools.eval.tests.test_planning import pilot
from tools.eval.tests.test_runner import script


@pytest.mark.parametrize("tariff", [None, Tariff(input=0, output=0), Tariff(input=3, output=4)])
def test_child_uses_only_explicit_arm_prices(tmp_path, monkeypatch, tariff):
    monkeypatch.setenv("MUSE_SPARK_INPUT_USD_PER_M", "0.000001")
    monkeypatch.setenv("MUSE_SPARK_OUTPUT_USD_PER_M", "0.000002")
    arm = OFF.model_copy(update={"tariff": tariff})
    env = server.environment(arm, tmp_path, 12345, 20)
    if tariff is None:
        assert "MUSE_SPARK_INPUT_USD_PER_M" not in env
        assert "MUSE_SPARK_OUTPUT_USD_PER_M" not in env
    else:
        assert env["MUSE_SPARK_INPUT_USD_PER_M"] == str(tariff.input)
        assert env["MUSE_SPARK_OUTPUT_USD_PER_M"] == str(tariff.output)


def test_baseten_allowance_requires_explicit_prices():
    with pytest.raises(BenchError, match=r"explicit.*tariff"):
        allowance("muse-spark-open", None)


@pytest.mark.parametrize("status,unknown", [("reserved", False), ("finished", True)])
@pytest.mark.parametrize("has_result", [False, True])
def test_incomplete_record_marks_its_repetition_and_aggregate_unknown(
    tmp_path, status, unknown, has_result
):
    ledger = Ledger(tmp_path / "results.jsonl", 2, None)
    try:
        first = pilot(ledger)
        assert first.result is not None
        for repetition in (1, 2):
            attempt = f"incomplete-{repetition}"
            result = first.result.model_copy(
                update={"repetition": repetition, "attempt_id": attempt}
            )
            ledger.state.records.append(
                first.model_copy(
                    update={
                        "attempt_id": attempt,
                        "repetition": repetition,
                        "status": status,
                        "unknown": unknown,
                        "result": result if has_result else None,
                    }
                )
            )
        arm = report(ledger)["arms"][OFF.arm_id]
        assert set(arm["repetitions"]) == {"1", "2"}
        for metrics in [arm["aggregate"], *arm["repetitions"].values()]:
            assert metrics["cost_usd"] == "unknown"
            assert metrics["tokens"] == {"input": "unknown", "output": "unknown"}
    finally:
        ledger.close()


def assert_reserved_socket(args, kwargs):
    assert "pass_fds" in kwargs
    fd = int(args[-1])
    assert kwargs["pass_fds"] == (fd,)
    with socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM) as inherited:
        address = inherited.getsockname()
        assert address == ("127.0.0.1", int(kwargs["env"]["OVERSIGHT_PORT"]))
        with socket.socket() as competitor:
            with pytest.raises(OSError) as error:
                competitor.bind(address)
            assert error.value.errno == errno.EADDRINUSE
    return fd


@pytest.mark.anyio
async def test_child_inherits_reserved_socket_and_serves_on_it(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    descriptors = []

    async def spawn(*args, **kwargs):
        descriptors.append(assert_reserved_socket(args, kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", spawn)
    fake_script = tmp_path / "script.json"
    fake_script.write_text(script())
    async with (
        server.dedicated(OFF, tmp_path, 1, fake_script) as url,
        httpx.AsyncClient(trust_env=False) as client,
    ):
        assert (await client.get(url + "/graph")).status_code == 200
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])


@pytest.mark.anyio
async def test_reserved_socket_closes_when_child_start_fails(tmp_path, monkeypatch):
    descriptors = []

    async def fail(*args, **kwargs):
        descriptors.append(assert_reserved_socket(args, kwargs))
        raise OSError("scripted spawn failure")

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fail)
    fake_script = tmp_path / "script.json"
    fake_script.write_text(script())
    with pytest.raises(OSError, match="scripted spawn failure"):
        async with server.dedicated(OFF, tmp_path, 1, fake_script):
            pytest.fail("a failed spawn must never become ready")
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])
