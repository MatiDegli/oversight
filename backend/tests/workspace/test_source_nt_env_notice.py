"""Typed hardlink refusals carry only a fixed remedy to the startup notice."""

import pytest

from oversight.workspace import core
from oversight.workspace import source_nt as nt

from .test_source_nt_env import environment
from .test_source_nt_reset import opened

HARDLINK_NOTICE = (
    "Own-source environment unavailable: the original venv is hard-linked "
    "(uv's default on Windows); recreate it with `uv sync --reinstall --link-mode copy` "
    "(or set UV_LINK_MODE=copy)."
)
GENERIC_NOTICE = (
    "Own-source environment unavailable: missing, unsafe or incompatible original venv."
)


@pytest.mark.parametrize("hardlink", [True, False])
def test_prepare_environment_sanitizes_typed_refusals(tmp_path, monkeypatch, hardlink):
    source, tree, io = environment(tmp_path)
    error_type = nt.HardlinkedEnvironmentError if hardlink else OSError

    def refuse(*args, **kwargs):
        raise error_type("private path and interpreter output must not escape")

    monkeypatch.setattr(io, "check", refuse)
    with pytest.raises(error_type) as caught:
        nt.prepare_environment(source, tree, io.deadline, io=io)
    assert type(caught.value) is error_type
    assert str(caught.value) == (
        HARDLINK_NOTICE
        if hardlink
        else "Own-source environment unavailable: missing or unsafe original venv"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.anyio
@pytest.mark.parametrize("hardlink", [True, False])
async def test_readiness_uses_exception_type_only(tmp_path, monkeypatch, hardlink):
    async with opened(tmp_path) as (workspace, _, _):
        error_type = nt.HardlinkedEnvironmentError if hardlink else OSError

        def refuse(*args):
            raise error_type("private path; environment file is hard-linked; interpreter output")

        async def forbidden(*args, **kwargs):
            pytest.fail("an unavailable original venv must not launch the probe")

        monkeypatch.setattr(workspace, "prepare_own_environment", refuse)
        monkeypatch.setattr(core, "probe_environment", forbidden)
        assert await workspace.own_environment_readiness() == (
            HARDLINK_NOTICE if hardlink else GENERIC_NOTICE
        )


@pytest.mark.anyio
async def test_real_hardlink_refusal_reaches_readiness_as_fixed_notice(tmp_path, monkeypatch):
    source, tree, io = environment(tmp_path / "environment")
    config = source.root / ".venv/pyvenv.cfg"
    config.with_name("linked.cfg").hardlink_to(config)
    before = config.read_bytes()
    async with opened(tmp_path) as (workspace, _, _):

        def prepare(root, deadline):
            return nt.prepare_environment(source, tree, deadline, io=io)

        async def forbidden(*args, **kwargs):
            pytest.fail("a hard-linked original venv must not launch the probe")

        monkeypatch.setattr(workspace, "prepare_own_environment", prepare)
        monkeypatch.setattr(core, "probe_environment", forbidden)
        assert await workspace.own_environment_readiness() == HARDLINK_NOTICE
    assert config.read_bytes() == before
