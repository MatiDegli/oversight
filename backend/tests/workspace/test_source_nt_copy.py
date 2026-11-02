"""Private COPY handoffs: real Git on Linux and native NT, with no public selector."""

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from oversight.graph import WorkspaceGraph
from oversight.workspace import run_process
from oversight.workspace import source_nt as nt
from oversight.workspace._platform import case_sensitive_directory, executable, stream_names
from oversight.workspace.files import open_nt
from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult

from .test_source_nt import source_case, unnamed


async def git(root: Path, *args: str) -> str:
    result = await run_process(
        [executable("git"), "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", *args],
        cwd=root,
        timeout_s=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


async def source_git(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    output_cap: int = 262144,
) -> ProcessResult:
    return await run_process(
        [executable("git"), *argv[1:]],
        cwd=cwd,
        timeout_s=timeout_s,
        env=dict(env or {}) | {"GIT_CONFIG_GLOBAL": os.devnull},
        output_cap=output_cap,
    )


async def fixture(source: nt.NtSource) -> dict[str, bytes]:
    root = source.root
    await git(root, "init", "-q", "--template=")
    files = {
        "a.py": b"x=1\r\n",
        "gone.py": b"gone",
        "ignored.py": b"tracked",
        "nested/.gitattributes": b"* eol=crlf\n",
        "nested/code.py": b"value = 1\r\n",
        "data.bin": b"\x00\xff\r\n",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    await git(root, "add", "-A")
    await git(root, "commit", "-qm", "Fixture")
    (root / "gone.py").unlink()
    files.pop("gone.py")
    files |= {
        "a.py": b"dirty\r\n",
        "new.py": b"new\r\n",
        "[literal].txt": b"brackets",
        "-option": b"dash",
        "caf\u00e9.txt": b"unicode",
        ".gitignore": b"ignored.py\n.venv/\nnode_modules/\n",
    }
    for name, data in files.items():
        (root / name).write_bytes(data)
    (root / "a.py").chmod(0o444)
    config = root / ".git/config"
    config.write_bytes(config.read_bytes() + b'\n[filter "evil"]\n clean = touch FILTER_SENTINEL\n')
    return files


async def selection(source: nt.NtSource, fd: int) -> nt.Selection:
    listing = await nt.inventory(
        source, git=r"C:\Git\git.exe", lock_fd=fd, spawn=source_git, ancestors=()
    )
    return nt.validate_selection(
        listing,
        sensitive=case_sensitive_directory if sys.platform == "win32" else lambda _: False,
        streams=stream_names if sys.platform == "win32" else unnamed,
    )


async def stage(source: nt.NtSource, fd: int) -> nt.StagedSource:
    return await nt.stage_source(
        await selection(source, fd),
        git=r"C:\Git\git.exe",
        lock_fd=fd,
        spawn=source_git,
        ancestors=(),
        sensitive=case_sensitive_directory if sys.platform == "win32" else lambda _: False,
        streams=stream_names if sys.platform == "win32" else unnamed,
    )


@pytest.mark.anyio
async def test_source_copy_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with source_case(tmp_path) as (source, fd):
        files = await fixture(source)
        for name in (".venv", "node_modules"):
            (source.root / name).mkdir()
            (source.root / name / "private.py").write_bytes(b"excluded")
            os.link(source.root / name / "private.py", source.root / name / "hardlink.py")
        ignored = tmp_path / "ignored"
        ignored.mkdir()
        junction = source.root / ".venv" / "junction"
        if sys.platform == "win32":
            result = await run_process(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(ignored)],
                cwd=tmp_path,
                timeout_s=10,
            )
            assert result.returncode == 0, result.stderr
        else:
            junction.symlink_to(ignored, target_is_directory=True)
        original = open_nt

        def observed(path: Path):
            assert not {".venv", "node_modules"} & set(path.parts)
            return original(path)

        monkeypatch.setattr(nt, "open_nt", observed)
        metadata = {
            name: (source.root / ".git" / name).read_bytes() for name in ("HEAD", "index", "config")
        }
        copied = await stage(source, fd)
        try:
            assert copied.manifest.read_bytes() == b"".join(
                n.encode() + b"\0" for n in sorted(files)
            )
            assert sorted(
                p.relative_to(copied.tree).as_posix() for p in copied.tree.rglob("*") if p.is_file()
            ) == sorted(files)
            for name, data in files.items():
                assert (copied.tree / name).read_bytes() == data
            snapshot = await offload(
                lambda: WorkspaceGraph(copied.tree).scan(copied.selection.inventory.head)
            )
            assert {node.path for node in snapshot.nodes} <= set(files)
            assert (copied.tree / "a.py").stat().st_mode & 0o200
            assert metadata == {n: (source.root / ".git" / n).read_bytes() for n in metadata}
            assert not (source.root / "FILTER_SENTINEL").exists()
        finally:
            copied.cleanup()
        assert not copied.root.exists()
        assert not (source.runtime / "workspace").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["file", "directory", "hash", "index", "HEAD", "selection"])
async def test_copy_mutation_preserves_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    with source_case(tmp_path) as (source, fd):
        await fixture(source)
        prior = source.runtime / "workspace"
        prior.mkdir()
        (prior / "keep").write_bytes(b"prior")
        before = nt.copy_selection

        def changed(copy: nt.StagedSource, deadline: float, clock):
            before(copy, deadline, clock)
            if mutation == "directory":
                (source.root / "nested").rename(source.root / "old")
                (source.root / "nested").mkdir()
            elif mutation in {"index", "HEAD"}:
                path = source.root / ".git" / mutation
                path.write_bytes(path.read_bytes() + b"changed")
            else:
                path = source.root / ("extra.py" if mutation == "selection" else "data.bin")
                if mutation == "file":
                    path.unlink()
                path.write_bytes(b"mutation")

        monkeypatch.setattr(nt, "copy_selection", changed)
        with pytest.raises(OSError, match="Windows source refused:"):
            await stage(source, fd)
        assert (prior / "keep").read_bytes() == b"prior"
        assert not list(source.runtime.glob("source-stage-*"))


@pytest.mark.anyio
async def test_copy_policy_refusal_and_independent_deadline(tmp_path: Path) -> None:
    with source_case(tmp_path) as (source, fd):
        await fixture(source)
        checked = await selection(source, fd)
        checked = replace(checked, inventory=replace(checked.inventory, deadline=0))
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
        (source.root / "nested/.gitattributes").write_bytes(b"* filter=evil\n")
        with pytest.raises(OSError, match="Windows source refused:"):
            await stage(source, fd)
        assert not list(source.runtime.glob("source-stage-*"))
        assert not (source.root / "FILTER_SENTINEL").exists()


@pytest.mark.anyio
async def test_staging_tamper_and_owned_cleanup(tmp_path: Path) -> None:
    import time

    with source_case(tmp_path) as (source, fd):
        await fixture(source)
        sibling = source.runtime / "source-stage-unowned"
        sibling.mkdir()
        (sibling / "keep").write_bytes(b"outside")
        copied = await stage(source, fd)
        nt.verify_stage(copied, source.runtime, time.monotonic() + 10)
        (copied.tree / "data.bin").write_bytes(b"tampered")
        with pytest.raises(OSError, match="staging bytes changed"):
            nt.verify_stage(copied, source.runtime, time.monotonic() + 10)
        copied.cleanup()
        assert (sibling / "keep").read_bytes() == b"outside"


@pytest.mark.anyio
async def test_max_manifest_bounded_add(tmp_path: Path) -> None:
    import time

    from oversight.workspace.metadata_io import Entry, replace_file

    with source_case(tmp_path) as (source, _):
        names = (
            ".gitignore",
            "[literal]",
            "-option",
            "caf\u00e9",
            *(str(i) for i in range(nt.MAX_PATHS - 4)),
        )
        assert len(nt.path_set(names)) == 16384
        for name in names:
            (source.root / name).write_bytes(b"*\n" if name == ".gitignore" else b"")
        from .test_source_nt import listing

        entries = tuple(nt.ValidatedEntry(n, None, "present", 0) for n in names)
        checked = nt.Selection(listing(source, names), entries, (), 0)
        manifest, deadline = source.runtime / "manifest", time.monotonic() + 10
        data = Entry(0o600, nt.manifest_bytes(checked))
        replace_file(manifest, None, data, deadline, cas=False)
        await git(source.root, "init", "-q", "--template=")
        option = f"--pathspec-from-file={manifest}"
        argv = [executable("git"), "add", "-f", option, "--pathspec-file-nul"]
        result = await run_process(
            argv, cwd=source.root, env={"GIT_LITERAL_PATHSPECS": "1"}, timeout_s=10
        )
        assert result.returncode == 0, result.stderr
        assert len(argv) == 5
        assert set((await git(source.root, "ls-files", "-z")).strip("\0").split("\0")) == set(names)
