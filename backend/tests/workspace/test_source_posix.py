"""Independent Linux source leaves, exercised without application composition."""

import asyncio
import hashlib
import os
import shutil
import stat
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from oversight.workspace import source_posix as posix
from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult, run_process

GIT = shutil.which("git") or "/usr/bin/git"


async def git(root: Path, *args: str) -> str:
    result = await run_process(
        [GIT, "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", *args],
        cwd=root,
        timeout_s=10,
        env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


async def repository(tmp_path: Path) -> posix.PosixSource:
    source, runtime, ide = (tmp_path / name for name in ("source", "runtime", "ide"))
    for root in (source, runtime, ide):
        root.mkdir()
    await git(source, "init", "-q", "--template=")
    (source / "a.py").write_text("value = 1\n")
    (source / "gone.py").write_text("gone\n")
    await git(source, "add", ".")
    await git(source, "commit", "-qm", "Fixture")
    return await posix.prepare_source(source, runtime, ide)


def witness(root: Path) -> tuple[tuple[str, tuple[int, ...], str], ...]:
    result: list[tuple[str, tuple[int, ...], str]] = []
    for path in (root, *sorted(root.rglob("*"))):
        info = path.lstat()
        data = (
            path.read_bytes()
            if stat.S_ISREG(info.st_mode)
            else (os.readlink(path).encode() if stat.S_ISLNK(info.st_mode) else b"")
        )
        result.append(
            (str(path.relative_to(root)), posix.token(info), hashlib.sha256(data).hexdigest())
        )
    return tuple(result)


def test_independent_interfaces():
    for cls in (posix.PosixSource, posix.Inventory, posix.Selection, posix.StagedSource):
        assert vars(cls)["__dataclass_params__"].frozen


@pytest.mark.parametrize(
    "kind",
    ["equal", "source-parent", "runtime-parent", "ide", "relative", "symlink", "unsafe", "swap"],
)
def test_posix_placement_refusals(tmp_path, kind):
    async def scenario():
        source, runtime, ide = (tmp_path / n for n in ("source", "runtime", "ide"))
        for root in (source, runtime, ide):
            root.mkdir()
        if kind == "equal":
            runtime = source
        elif kind == "source-parent":
            runtime = source / "child"
            runtime.mkdir()
        elif kind == "runtime-parent":
            source = runtime / "child"
            source.mkdir()
        elif kind == "ide":
            source = ide
        elif kind == "relative":
            source = Path("relative")
        elif kind == "symlink":
            source.rmdir()
            source.symlink_to(ide, target_is_directory=True)
        elif kind == "unsafe":
            source.chmod(0o777)
        if kind == "swap":
            prepared = await posix.prepare_source(source, runtime, ide)
            source.rename(tmp_path / "old-source")
            source.mkdir()
            with pytest.raises(OSError):
                await posix.inventory(prepared, git=GIT)
        else:
            with pytest.raises(OSError):
                await posix.prepare_source(source, runtime, ide)
        assert not tuple(runtime.glob("source-stage-*"))

    asyncio.run(scenario())


def test_posix_inventory_safety(tmp_path):
    async def scenario():
        source = await repository(tmp_path)
        root = source.root
        (root / "a.py").write_text("working edit\r\n")
        (root / "gone.py").unlink()
        (root / ".gitignore").write_text("ignored\na.py\n.venv/\nnode_modules/\n")
        for name in ("new.py", "A.py", "ignored", ".env", "key.pem", ".env.example"):
            (root / name).write_text(name)
        for name in (".venv", "node_modules", "__pycache__", "dist"):
            (root / name).mkdir()
            (root / name / "excluded").write_text("private")
        await git(root, "add", "--force", ".env", ".venv/excluded", "node_modules/excluded")
        before = witness(root)
        listing = await posix.inventory(source, git=GIT)
        selected = await posix.validate_selection(listing)
        assert set(listing.paths) == {
            "a.py",
            "A.py",
            "gone.py",
            "new.py",
            ".gitignore",
            ".env.example",
        }
        assert next(e for e in selected.entries if e.path == "gone.py").sha256 is None
        assert next(e for e in selected.entries if e.path == "a.py").size == len(
            b"working edit\r\n"
        )
        assert witness(root) == before

    asyncio.run(scenario())


def test_packed_clone_inventory_and_copy(tmp_path):
    async def scenario():
        original = await repository(tmp_path)
        historical = original.root / "historical.bin"
        historical.write_bytes(os.urandom(posix.MAX_FILE + 65536))
        await git(original.root, "add", "historical.bin")
        await git(original.root, "commit", "-qm", "Historical blob")
        historical.unlink()
        await git(original.root, "add", "-u")
        await git(original.root, "commit", "-qm", "Remove historical blob")
        clone = tmp_path / "clone"
        await git(tmp_path, "clone", "--no-local", "--quiet", str(original.root), str(clone))
        assert any(p.stat().st_size > posix.MAX_FILE for p in (clone / ".git").rglob("*.pack"))
        # Reflogs and object storage are witnesses, outside the working-file byte budget.
        with (clone / ".git/logs/large").open("wb") as log:
            log.truncate(posix.MAX_TOTAL + 1)
        source = await posix.prepare_source(clone, original.runtime, original.ide)
        before = {p: posix.token(p.lstat()) for p in clone.rglob("*")}
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        assert selection.total_bytes == len(b"value = 1\ngone\n")
        stage = await posix.stage_source(selection, git=GIT)
        await posix.verify_stage(stage)
        assert (stage.tree / "a.py").read_bytes() == b"value = 1\n"
        assert not (stage.tree / ".git").exists()
        assert {p: posix.token(p.lstat()) for p in clone.rglob("*")} == before
        await posix.cleanup_stage(stage)
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


def test_git_metadata_hashes_only_head_and_index(tmp_path, monkeypatch):
    async def scenario():
        source = await repository(tmp_path)
        read = posix.read_file
        reads = set()

        def record_read(path, *args, **kwargs):
            reads.add(path.relative_to(source.root / ".git").as_posix())
            return read(path, *args, **kwargs)

        monkeypatch.setattr(posix, "read_file", record_read)
        listing = await posix.inventory(source, git=GIT)
        assert reads == {"HEAD", "index"}
        for item in listing.metadata:
            if item.path in {"HEAD", "index"}:
                assert (
                    item.sha256
                    == hashlib.sha256((source.root / ".git" / item.path).read_bytes()).hexdigest()
                )
            else:
                assert item.sha256 is None

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["selection", "copy"])
@pytest.mark.parametrize("attack", ["bytes", "swap", "missing", "extra", "mode"])
@pytest.mark.parametrize("storage", ["loose", "packed"])
def test_git_object_changes_refuse(tmp_path, monkeypatch, phase, attack, storage):
    async def scenario():
        source = await repository(tmp_path)
        blob = await git(source.root, "rev-parse", "HEAD:a.py")
        target = source.root / ".git/objects" / blob[:2] / blob[2:]
        if storage == "packed":
            await git(source.root, "repack", "-adq")
            target = next((source.root / ".git/objects/pack").glob("*.pack"))
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))

        def mutate():
            if attack == "bytes":
                target.chmod(0o600)
                data = target.read_bytes()
                target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            elif attack == "swap":
                target.rename(tmp_path / "held-object")
                shutil.copyfile(tmp_path / "held-object", target)
            elif attack == "missing":
                target.unlink()
            elif attack == "extra":
                (target.parent / "extra").write_bytes(b"extra")
            else:
                target.chmod(0o600)

        if phase == "selection":
            mutate()
        else:
            copy = posix._copy

            def changed_copy(*args):
                stage = copy(*args)
                mutate()
                return stage

            monkeypatch.setattr(posix, "_copy", changed_copy)
        with pytest.raises(OSError, match="Git metadata changed"):
            await posix.stage_source(selection, git=GIT)
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize("attack", ["link", "fifo", "hardlink", "writable"])
def test_git_object_storage_safety(tmp_path, attack):
    async def scenario():
        source = await repository(tmp_path)
        target = source.root / ".git/objects/pack/unsafe"
        if attack == "link":
            target.symlink_to(source.root / "a.py")
        elif attack == "fifo":
            os.mkfifo(target)
        elif attack == "hardlink":
            os.link(source.root / "a.py", target)
        else:
            target.write_bytes(b"unsafe")
            target.chmod(0o666)

        async def spawn(*args, **kwargs):
            raise AssertionError("unsafe object storage reached Git")

        with pytest.raises(OSError):
            await posix.inventory(source, git=GIT, spawn=spawn)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack",
    [
        "link",
        "hardlink",
        "fifo",
        "nested",
        "submodule",
        "gitdir",
        "metadata-link",
        "include",
        "fsmonitor",
        "alternates",
        "attributes",
        "info-attributes",
    ],
)
def test_hostile_inventory(tmp_path, attack):
    async def scenario():
        source = await repository(tmp_path)
        root = source.root
        if attack == "link":
            (root / "linked").symlink_to(root / "a.py")
        elif attack == "hardlink":
            os.link(root / "a.py", root / "linked")
        elif attack == "fifo":
            os.mkfifo(root / "fifo")
        elif attack == "nested":
            (root / "nested/.git").mkdir(parents=True)
        elif attack == "submodule":
            (root / ".gitmodules").write_text("unsafe")
        elif attack == "gitdir":
            (root / ".git").rename(root / "real-git")
            (root / ".git").write_text("gitdir: real-git\n")
        elif attack == "metadata-link":
            (root / ".git/HEAD").unlink()
            (root / ".git/HEAD").symlink_to(root / "a.py")
        elif attack in {"include", "fsmonitor"}:
            await git(
                root,
                "config",
                "include.path" if attack == "include" else "core.fsmonitor",
                "/should-never-execute",
            )
        elif attack == "alternates":
            (root / ".git/objects/info/alternates").write_text("/elsewhere\n")
        elif attack == "attributes":
            (root / ".gitattributes").write_text("* filter=execute\n")
        elif attack == "info-attributes":
            (root / ".git/info").mkdir(exist_ok=True)
            (root / ".git/info/attributes").write_text("* ident\n")
        with pytest.raises(OSError):
            listing = await posix.inventory(source, git=GIT)
            await posix.validate_selection(listing)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack", ["failed", "timeout", "truncated", "malformed", "start", "cancel"]
)
def test_git_process_refusals(tmp_path, attack):
    async def scenario():
        source = await repository(tmp_path)
        before = witness(source.root)

        async def spawn(argv, **kwargs):
            assert 0 < kwargs["timeout_s"] <= posix.PHASE_SECONDS
            assert kwargs["output_cap"] <= posix.MAX_GIT
            if attack == "start":
                raise OSError("failed start")
            if attack == "cancel":
                raise asyncio.CancelledError
            return ProcessResult(
                1 if attack == "failed" else 0,
                "broken",
                "",
                attack == "timeout",
                attack == "truncated",
                0,
            )

        with pytest.raises(asyncio.CancelledError if attack == "cancel" else OSError):
            await posix.inventory(source, git=GIT, spawn=spawn)
        assert witness(source.root) == before
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


def test_posix_staged_copy_verification(tmp_path):
    async def scenario():
        source = await repository(tmp_path)
        (source.root / "data.bin").write_bytes(b"\0\xff\r\n")
        (source.root / ".gitattributes").write_text("* text eol=crlf\n")
        (source.root / "a.py").chmod(0o755)
        (source.root / "gone.py").unlink()
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        stage = await posix.stage_source(selection, git=GIT)
        await posix.verify_stage(stage)
        assert (stage.tree / "a.py").read_bytes() == (source.root / "a.py").read_bytes()
        assert not (stage.tree / "gone.py").exists()
        assert (stage.tree / "a.py").stat().st_ino != (source.root / "a.py").stat().st_ino
        assert stage.manifest.read_bytes() == posix.manifest_bytes(selection)
        assert (stage.tree / "data.bin").read_bytes() == b"\0\xff\r\n"
        assert (stage.tree / "a.py").stat().st_mode & 0o111
        await posix.cleanup_stage(stage)
        assert not stage.root.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack", ["bytes", "extra", "missing", "swap", "manifest", "directory", "root"]
)
def test_stage_tampering(tmp_path, attack):
    async def scenario():
        source = await repository(tmp_path)
        stage = await posix.stage_source(
            await posix.validate_selection(await posix.inventory(source, git=GIT)), git=GIT
        )
        target = stage.tree / "a.py"
        if attack == "bytes":
            target.write_text("tamper")
        elif attack == "extra":
            (stage.tree / "extra").write_text("extra")
        elif attack == "missing":
            target.unlink()
        elif attack == "swap":
            target.rename(tmp_path / "held-file")
            target.write_text("value = 1\n")
        elif attack == "manifest":
            stage.manifest.write_text("bad")
        elif attack == "directory":
            stage.tree.rename(tmp_path / "held-tree")
            stage.tree.mkdir()
        elif attack == "root":
            stage.root.rename(tmp_path / "held-stage")
            stage.root.mkdir()
        with pytest.raises(OSError):
            await posix.verify_stage(stage)
        if attack in {"swap", "directory", "root"}:
            with pytest.raises(OSError):
                await posix.cleanup_stage(stage)
            assert target.exists() if attack == "swap" else stage.root.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("attack", ["bytes", "index", "config", "addition", "swap", "deadline"])
def test_source_races(tmp_path, attack):
    async def scenario():
        source = await repository(tmp_path)
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        if attack == "bytes":
            (source.root / "a.py").write_text("changed")
        elif attack == "index":
            await git(source.root, "update-index", "--assume-unchanged", "a.py")
        elif attack == "config":
            await git(source.root, "config", "user.name", "Changed")
        elif attack == "addition":
            (source.root / "added.py").write_text("added")
        elif attack == "swap":
            source.root.rename(tmp_path / "held-source")
            source.root.mkdir()
        else:
            selection = replace(selection, inventory=replace(selection.inventory, deadline=0))
            with pytest.raises(OSError):
                await posix.validate_selection(selection.inventory)
            return
        with pytest.raises(OSError):
            await posix.stage_source(selection, git=GIT)
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "refusal", "cancel"])
def test_posix_original_untouched(tmp_path, outcome):
    async def scenario():
        source = await repository(tmp_path)
        (source.root / ".venv").mkdir()
        (source.root / ".venv/witness").write_text("environment")
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        before = witness(source.root)

        async def spawn(argv, **kwargs):
            if outcome == "cancel":
                raise asyncio.CancelledError
            return ProcessResult(1, "", "", False, False, 0)

        if outcome == "success":
            stage = await posix.stage_source(selection, git=GIT)
            (stage.tree / "a.py").write_text("disposable edit")
            await posix.cleanup_stage(stage)
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancel" else OSError):
                await posix.stage_source(selection, git=GIT, spawn=spawn)
        assert witness(source.root) == before
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack",
    [
        "source-bytes",
        "head",
        "index",
        "addition",
        "stage-bytes",
        "stage-extra",
        "stage-missing",
        "stage-swap",
        "cancel",
    ],
)
def test_copy_rechecks_and_cancellation(tmp_path, monkeypatch, attack):
    async def scenario():
        source = await repository(tmp_path)
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        before = witness(source.root)
        copy = posix._copy
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def changed_copy(selection, root, owned, deadline):
            stage = copy(selection, root, owned, deadline)
            if attack == "source-bytes":
                (source.root / "a.py").write_text("raced")
            elif attack in {"head", "index"}:
                path = source.root / ".git" / ("HEAD" if attack == "head" else "index")
                path.write_bytes(path.read_bytes() + b"raced")
            elif attack == "addition":
                (source.root / "new.py").write_text("raced")
            elif attack == "stage-bytes":
                (stage.tree / "a.py").write_text("raced")
            elif attack == "stage-extra":
                (stage.tree / "extra").write_text("retain unknown")
            elif attack == "stage-missing":
                (stage.tree / "a.py").unlink()
            elif attack == "stage-swap":
                (stage.tree / "a.py").rename(tmp_path / "held-stage-file")
                (stage.tree / "a.py").write_text("retain replacement")
            else:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
            return stage

        monkeypatch.setattr(posix, "_copy", changed_copy)
        if attack == "cancel":
            task = asyncio.create_task(posix.stage_source(selection, git=GIT))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(OSError):
                await posix.stage_source(selection, git=GIT)
        if attack.startswith("stage") or attack == "cancel":
            assert witness(source.root) == before
        if attack == "stage-extra":
            assert (
                next(source.runtime.glob("source-stage-*/tree/extra")).read_text()
                == "retain unknown"
            )
        elif attack == "stage-swap":
            assert (
                next(source.runtime.glob("source-stage-*/tree/a.py")).read_text()
                == "retain replacement"
            )
        else:
            assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "query,output",
    [
        ("rev-parse", "invalid\n"),
        ("index", "160000 " + "1" * 40 + " 0\tsubmodule\0"),
        ("index", "100644 " + "1" * 40 + " 1\tconflict\0"),
        ("index", "100644 " + "1" * 40 + " 0\t../escape\0"),
        ("index", "100644 " + "1" * 40 + " 0\ta.py"),
        ("paths", "a.py\0"),
        ("paths", "a.py\0gone.py\0a.py\0"),
        ("paths", "a.py\0gone.py\0bad\ufffd\0"),
    ],
)
def test_malformed_git_queries(tmp_path, query, output):
    async def scenario():
        source = await repository(tmp_path)

        async def spawn(argv, **kwargs):
            selected = (
                "rev-parse"
                if "rev-parse" in argv
                else ("index" if "-s" in argv else "paths" if "ls-files" in argv else "config")
            )
            if selected == query:
                return ProcessResult(0, output, "", False, False, 0)
            return await run_process(argv, **kwargs)

        with pytest.raises(OSError):
            await posix.inventory(source, git=GIT, spawn=spawn)

    asyncio.run(scenario())


@pytest.mark.parametrize("budget", ["file", "total", "paths"])
def test_inventory_budgets(tmp_path, monkeypatch, budget):
    async def scenario():
        source = await repository(tmp_path)
        if budget == "paths":
            monkeypatch.setattr(posix, "MAX_PATHS", 2)
        elif budget == "file":
            with (source.root / "large").open("wb") as f:
                f.truncate(posix.MAX_FILE + 1)
        else:
            monkeypatch.setattr(posix, "MAX_TOTAL", 1)
        with pytest.raises(OSError):
            await posix.validate_selection(await posix.inventory(source, git=GIT))

    asyncio.run(scenario())


def test_descriptor_closure(tmp_path):
    async def scenario():
        source = await repository(tmp_path)
        before = await offload(lambda: len(tuple(Path("/proc/self/fd").iterdir())))
        for _ in range(3):
            stage = await posix.stage_source(
                await posix.validate_selection(await posix.inventory(source, git=GIT)), git=GIT
            )
            await posix.verify_stage(stage)
            await posix.cleanup_stage(stage)
        assert await offload(lambda: len(tuple(Path("/proc/self/fd").iterdir()))) == before

    asyncio.run(scenario())


def runner_child_code(marker: Path) -> str:
    return (
        "import os,time; from pathlib import Path; marker = Path("
        + repr(str(marker))
        + "); pending = marker.with_name(marker.name + '.tmp'); "
        + "pending.write_text(str(os.getpid())); pending.replace(marker); time.sleep(30)"
    )


def test_runner_pid_publication_is_atomic(tmp_path):
    async def scenario():
        marker = tmp_path / "child-pid"
        # Inspect the create-before-write window deterministically inside the child.
        probe = (
            "import os,time\nfrom pathlib import Path\n"
            + f"marker = Path({str(marker)!r})\n"
            + "def interrupted_write(path, data):\n"
            + " with path.open('w') as stream:\n"
            + "  assert not marker.exists(), 'partial PID published'\n"
            + "  return stream.write(data)\n"
            + "Path.write_text = interrupted_write\ntime.sleep = lambda _: None\n"
        )
        result = await run_process(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                probe + runner_child_code(marker) + "; print(os.getpid())",
            ],
            cwd=tmp_path,
            timeout_s=10,
        )
        assert result.returncode == 0, result.stderr
        assert marker.read_text() == result.stdout.strip()
        assert int(marker.read_text()) > 0

    asyncio.run(scenario())


async def runner_cleanup_witness(tmp_path, operation, mode):
    marker = tmp_path / "child-pid"
    descriptors = await offload(lambda: len(tuple(Path("/proc/self/fd").iterdir())))
    entered = asyncio.Event()

    async def spawn(argv, **kwargs):
        entered.set()
        return await run_process(
            [sys.executable, "-I", "-S", "-B", "-c", runner_child_code(marker)],
            cwd=kwargs["cwd"],
            timeout_s=0.3 if mode == "timeout" else 10,
            output_cap=kwargs["output_cap"],
            env=kwargs["env"],
        )

    task = asyncio.create_task(operation(spawn))
    if mode == "cancel":
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(1000):
            if marker.exists():
                break
            await asyncio.sleep(0.005)
        assert marker.exists()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else OSError):
        await task
    assert marker.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    assert await offload(lambda: len(tuple(Path("/proc/self/fd").iterdir()))) == descriptors


@pytest.mark.parametrize("phase", ["inventory", "stage"])
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_real_git_runner_cleanup(tmp_path, phase, mode):
    async def scenario():
        source = await repository(tmp_path)
        selection = await posix.validate_selection(await posix.inventory(source, git=GIT))
        before = witness(source.root)

        async def operation(spawn):
            if phase == "inventory":
                return await posix.inventory(source, git=GIT, spawn=spawn)
            return await posix.stage_source(selection, git=GIT, spawn=spawn)

        await runner_cleanup_witness(tmp_path, operation, mode)
        assert witness(source.root) == before
        assert not tuple(source.runtime.iterdir())

    asyncio.run(scenario())


def test_posix_case_and_ancestor_policy(tmp_path):
    async def scenario():
        source, runtime, ide = (tmp_path / name for name in ("source", "SOURCE", "ide"))
        for path in (source, runtime, ide):
            path.mkdir()
        await posix.prepare_source(source, runtime, ide)
        parent = tmp_path / "unsafe-parent"
        parent.mkdir()
        nested = parent / "source"
        nested.mkdir()
        parent.chmod(0o777)
        with pytest.raises(OSError):
            await posix.prepare_source(nested, runtime, ide)

    asyncio.run(scenario())


def test_posix_placement_bind_alias(tmp_path):
    async def scenario():
        source, alias, ide = (tmp_path / n for n in ("source", "alias", "ide"))
        for path in (source, alias, ide):
            path.mkdir()
        (source / "runtime").mkdir()
        before = witness(source)
        code = """import asyncio,sys
from pathlib import Path
from oversight.workspace.source_posix import prepare_source
from oversight.workspace.runner import run_process
async def main():
 source,alias,ide=map(Path,sys.argv[1:])
 result=await run_process(["/usr/bin/mount","--bind",str(source),str(alias)],cwd=ide,timeout_s=5)
 if result.returncode != 0: raise OSError(result.stderr)
 try:
  try: await prepare_source(source,alias/"runtime",ide)
  except OSError as error:
   assert "filesystem alias" in str(error), str(error)
   print("real bind alias refused")
  else: raise AssertionError("bind alias overlap accepted")
 finally:
  result=await run_process(["/usr/bin/umount",str(alias)],cwd=ide,timeout_s=5)
  assert result.returncode == 0, result.stderr
asyncio.run(main())"""
        result = await run_process(
            [
                "/usr/bin/unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--",
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                str(source),
                str(alias),
                str(ide),
            ],
            cwd=tmp_path,
            timeout_s=20,
            output_cap=8192,
        )
        assert result.returncode == 0, result.stderr
        assert "real bind alias refused" in result.stdout
        assert witness(source) == before
        assert not tuple(alias.iterdir())

    asyncio.run(scenario())
