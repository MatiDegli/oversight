"""Source budgets are independent of metadata budgets and cannot reach reset."""

from dataclasses import replace
from pathlib import Path

import pytest

from oversight.workspace import Workspace
from oversight.workspace import source_nt as nt

from .test_source_nt import Git, listing, source_case, unnamed


@pytest.mark.parametrize("kind", ["paths", "file", "total", "time"])
def test_source_budget_no_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    def reset(*args: object) -> None:
        pytest.fail("source budget entered reset")

    monkeypatch.setattr(Workspace, "reset", reset)
    with source_case(tmp_path) as (source, _):
        prior = source.runtime / "workspace"
        prior.mkdir()
        (prior / "keep").write_bytes(b"prior runtime")
        if kind == "paths":
            paths = tuple(f"a/{i}" for i in range(nt.MAX_PATHS - 1))
            assert len(nt.path_set(paths)) == nt.MAX_PATHS
            with pytest.raises(OSError, match="16,384"):
                nt.path_set((*paths, "a/extra"))
        else:
            count = 32 if kind == "total" else 1
            paths = tuple(f"{i}.py" for i in range(count))
            for path in paths:
                (source.root / path).write_bytes(b"x" * (nt.MAX_FILE if kind != "time" else 0))
            before = listing(source, paths)
            result = nt.validate_selection(
                before, sensitive=lambda _: False, streams=unnamed, clock=lambda: 10
            )
            assert result.total_bytes == (nt.MAX_FILE * count if kind != "time" else 0)
            if kind == "total":
                (source.root / "extra").write_bytes(b"x")
                before = replace(before, paths=(*paths, "extra"))
            elif kind == "file":
                (source.root / paths[0]).write_bytes(b"x" * (nt.MAX_FILE + 1))
            with pytest.raises(OSError, match=r"budget|4 MiB|deadline"):
                nt.validate_selection(
                    before,
                    sensitive=lambda _: False,
                    streams=unnamed,
                    clock=lambda: 10.001 if kind == "time" else 10,
                )
        assert (prior / "keep").read_bytes() == b"prior runtime"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fault", ["exact", "bytes", "failed", "timeout", "truncated", "malformed", "time"]
)
async def test_git_budget_no_reset(tmp_path: Path, fault: str) -> None:
    with source_case(tmp_path) as (source, fd):
        git = Git()

        git.result = lambda result: (
            replace(
                result,
                stderr="x" * (nt.MAX_GIT - len(result.stdout) + (fault == "bytes")),
                returncode=1 if fault == "failed" else 0,
                timed_out=fault == "timeout",
                truncated=fault == "truncated",
                stdout="bad" if fault == "malformed" else result.stdout,
            )
            if len(git.calls) == 1
            else result
        )

        call = nt.inventory(
            source,
            git=r"C:\Git\git.exe",
            lock_fd=fd,
            spawn=git,
            ancestors=(),
            clock=lambda: 0 if not git.calls else (10.001 if fault == "time" else 10),
        )
        if fault == "exact":
            assert (await call).head
        else:
            with pytest.raises(OSError):
                await call
        assert not (source.runtime / "workspace").exists()


def test_nt_long_path_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with source_case(tmp_path) as (source, _):
        projected = nt.projections(source, ("dir/file.py",))
        assert any("worktrees/agent-9999" in p for p in projected)
        assert any("base-probe-" in p for p in projected)
        for name in ("source-stage-", "own-source", "own-reset", "manifest", ".oversight-"):
            assert any(name in p for p in projected)
        for suffix in projected:
            units = len(suffix.encode("utf-16-le")) // 2
            nt.short_path("x" * (239 - units) + suffix)
            with pytest.raises(OSError, match="240 UTF-16"):
                nt.short_path("x" * (240 - units) + suffix)
        nt.short_path("x" * 237 + "😀")
        with pytest.raises(OSError, match="240 UTF-16"):
            nt.short_path("x" * 238 + "😀")
        monkeypatch.setattr(
            "oversight.workspace.source_nt.resolve_in",
            lambda *a, **k: pytest.fail("resolve before length refusal"),
        )
        with pytest.raises(OSError, match="240 UTF-16"):
            nt.validate_selection(
                listing(replace(source, runtime_path="C:/" + "x" * 180), ("long/" + "x" * 100,)),
                sensitive=lambda _: False,
                streams=unnamed,
                clock=lambda: 0,
            )


@pytest.mark.anyio
@pytest.mark.parametrize("limit", ["file", "total", "time", "output", "metadata"])
async def test_copy_budget_no_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    from .test_source_nt_copy import fixture, selection, source_git

    with source_case(tmp_path) as (source, fd):
        await fixture(source)
        prior = source.runtime / "workspace"
        prior.mkdir()
        (prior / "keep").write_bytes(b"prior")
        checked = await selection(source, fd)
        monkeypatch.setattr(nt, "MAX_FILE", max(e.size for e in checked.entries))
        monkeypatch.setattr(nt, "MAX_TOTAL", checked.total_bytes)
        copied = await nt.stage_source(
            checked,
            git=r"C:\Git\git.exe",
            lock_fd=fd,
            spawn=source_git,
            ancestors=(),
            sensitive=lambda _: False,
            streams=unnamed,
        )
        copied.cleanup()
        if limit in {"file", "total"}:
            monkeypatch.setattr(
                nt,
                "MAX_FILE" if limit == "file" else "MAX_TOTAL",
                nt.MAX_FILE - 1 if limit == "file" else nt.MAX_TOTAL - 1,
            )
        if limit == "metadata":
            monkeypatch.setattr(nt, "MAX_METADATA_FILE", 1)
        elapsed = 0.0
        queries = 0
        original_copy = nt.copy_selection

        def copy_then_advance(staged: nt.StagedSource, deadline: float, clock):
            nonlocal elapsed
            original_copy(staged, deadline, clock)
            if limit == "time":
                assert staged.manifest.exists()
                elapsed = 9.0

        monkeypatch.setattr(nt, "copy_selection", copy_then_advance)

        async def output(*args, **kwargs):
            nonlocal elapsed, queries
            if limit == "time":
                # Relisting has only the last second of COPY, not a fresh deadline.
                assert kwargs["timeout_s"] == 1.0
            result = await source_git(*args, **kwargs)
            queries += 1
            if limit == "time":
                elapsed = 10.001
            return replace(result, stderr="x" * (nt.MAX_GIT + 1)) if limit == "output" else result

        def clock() -> float:
            return elapsed

        with pytest.raises(OSError, match="Windows source refused:"):
            await nt.stage_source(
                checked,
                git=r"C:\Git\git.exe",
                lock_fd=fd,
                spawn=output,
                ancestors=(),
                sensitive=lambda _: False,
                streams=unnamed,
                clock=clock,
            )
        if limit == "time":
            assert queries == 1
        assert (prior / "keep").read_bytes() == b"prior"
        assert not list(source.runtime.glob("source-stage-*"))


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [0, 1])
# Linear validation and staging of 16,384 paths need headroom on loaded hosts.
@pytest.mark.timeout(300)
async def test_copy_path_budget_no_reset(tmp_path: Path, extra: int) -> None:
    from .test_source_nt import HEAD

    with source_case(tmp_path) as (source, fd):
        for name in ("HEAD", "index"):
            (source.root / ".git" / name).write_bytes(b"pinned metadata")
        (source.root / "a.py").write_bytes(b"present")
        prior = source.runtime / "workspace"
        prior.mkdir()
        (prior / "keep").write_bytes(b"prior")
        # Missing tracked files still count, along with their missing ancestor.
        paths = tuple(sorted(("a.py", *(f"deleted/{i}" for i in range(nt.MAX_PATHS - 2)))))
        before = replace(
            listing(source, paths), metadata=nt.source_metadata(source.root, 10, lambda: 0)
        )
        checked = nt.validate_selection(
            before, sensitive=lambda _: False, streams=unnamed, clock=lambda: 0
        )
        assert len(nt.path_set(paths)) == 16_384
        if extra:
            paths = (*paths, "deleted/extra")
            checked = replace(
                checked,
                inventory=replace(before, paths=paths, tracked=frozenset(paths)),
                entries=(*checked.entries, nt.ValidatedEntry("deleted/extra", None, None, 0)),
            )
        git = Git(
            "\0".join(paths) + "\0",
            "".join(f"100644 {HEAD} 0\t{p}\0" for p in paths),
        )
        git.answers[-1] = ""
        call = nt.stage_source(
            checked,
            git=r"C:\Git\git.exe",
            lock_fd=fd,
            spawn=git,
            ancestors=(),
            sensitive=lambda _: False,
            streams=unnamed,
            clock=lambda: 0,
        )
        if extra:
            with pytest.raises(OSError, match="16,384"):
                await call
        else:
            copied = await call
            assert copied.manifest.read_bytes() == b"a.py\0"
            assert (copied.tree / "a.py").read_bytes() == b"present"
            copied.cleanup()
            assert len(git.calls) == 4
        assert (prior / "keep").read_bytes() == b"prior"
        assert not list(source.runtime.glob("source-stage-*"))
