"""Static preparation fixtures qualify leaf validation, never an actual mount boundary."""

import hashlib
import json
import os
import shutil
import struct
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from oversight.workspace import preview_preparation as preparation
from oversight.workspace.preview_preparation import (
    Inventory,
    InventoryEntry,
    PreparationInputs,
    ProjectImport,
    RootMapping,
    ToolIdentity,
    inventory_digest,
    prepare_preview,
    scrub_preview_env,
)
from oversight.workspace.runner import child_env, run_process

pytestmark = pytest.mark.anyio


def static_elf() -> bytes:
    data = bytearray(64)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", data, 16, 2)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 0)
    return bytes(data)


def record(root: Path, name: str, *, owned=False) -> Inventory:
    entries = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode & 0o777
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(InventoryEntry(relative, "symlink", mode=mode, target=os.readlink(path)))
        elif path.is_dir():
            entries.append(InventoryEntry(relative, "directory", mode=mode))
        else:
            data = path.read_bytes()
            entries.append(
                InventoryEntry(relative, "file", hashlib.sha256(data).hexdigest(), len(data), mode)
            )
    return Inventory(name, name, root, tuple(entries), inventory_digest(entries), owned)


@pytest.fixture
def inputs(tmp_path):
    tree, original = tmp_path / "T", tmp_path / "original"
    tree.mkdir()
    original.mkdir()
    storage = tmp_path / "prepared"
    storage.mkdir()
    inventories, mappings, tools, imports = [], [], [], []
    for name in ("backend", "contracts", "demo-test", "frontend", "tool", "system"):
        root = storage / name
        root.mkdir()
        target = Path("/deps") / name
        if name in {"backend", "contracts", "demo-test"}:
            (root / "pyvenv.cfg").write_text(
                "home = /deps/tool/bin\ninclude-system-site-packages = false\n"
            )
            site = root / "site"
            site.mkdir()
            package = name.replace("-", "_") + "_fixture"
            replacement = tree / name / "src"
            replacement.mkdir(parents=True)
            (replacement / (package + ".py")).write_text("VALUE = 'from T'\n")
            old = original / name
            old.mkdir()
            (site / "original.pth").write_text(str(old) + "\n")
            finder = "__editable___" + package
            (site / (finder + ".py")).write_text(
                f"MAPPING = {{{package!r}: {str(old / package)!r}}}\n"
                f"open({str(original / 'HOOK_RAN')!r}, 'w').write('bad')\n"
            )
            (site / "finder.pth").write_text(f"import {finder}; {finder}.install()\n")
            info = site / (package + ".dist-info")
            info.mkdir()
            (info / "direct_url.json").write_text(
                json.dumps({"url": old.as_uri(), "dir_info": {"editable": True}})
            )
            imports.append(
                ProjectImport(target / "site", old, Path("/workspace") / name / "src", (package,))
            )
        elif name == "tool":
            (root / "bin").mkdir()
            for tool in ("python", "uv-python", "uv", "node", "pnpm"):
                executable = root / "bin" / tool
                executable.write_bytes(static_elf())
                executable.chmod(0o755)
                tools.append(
                    ToolIdentity(
                        tool,
                        target / "bin" / tool,
                        hashlib.sha256(executable.read_bytes()).hexdigest(),
                    )
                )
        else:
            (root / "entry").write_text("recorded system data")
        inventories.append(record(root, name, owned=True))
        mappings.append(RootMapping(root, target))
    mappings.append(RootMapping(tree, Path("/workspace"), False))
    return PreparationInputs(
        tree=tree,
        excluded_roots=(original, tmp_path / "host-home"),
        preview_directory=tmp_path / "runtime/previews/example",
        preparation_storage=storage,
        inventories=tuple(inventories),
        tools=tuple(tools),
        system_entries=(Path("/deps/system/entry"),),
        root_mappings=tuple(mappings),
        imports=tuple(imports),
        project_environment=Path("/deps/backend"),
        demo_python=Path("/deps/tool/bin/python"),
        resources=(("memory_bytes", 123456789),),
    )


async def never_spawn(*args, **kwargs):
    pytest.fail("preparation must never run installation, synchronization or tool hooks")


@pytest.mark.parametrize("fault", ["missing", "truncated", "bytes", "identity", "escape", "extra"])
async def test_prepared_manifests_and_tool_identity(inputs, fault):
    bad = inputs.inventories[0]
    untouched = inputs.inventories[1].root
    if fault == "missing":
        inputs = replace(inputs, inventories=inputs.inventories[1:])
    elif fault == "truncated":
        inputs = replace(
            inputs, inventories=(replace(bad, entries=bad.entries[:-1]), *inputs.inventories[1:])
        )
    elif fault == "bytes":
        (bad.root / "site/original.pth").write_text("changed")
    elif fault == "identity":
        inputs = replace(
            inputs, tools=(replace(inputs.tools[0], sha256="0" * 64), *inputs.tools[1:])
        )
    elif fault == "escape":
        (bad.root / "unsafe").symlink_to(inputs.excluded_roots[0])
        replacement = record(bad.root, bad.name, owned=True)
        inputs = replace(inputs, inventories=(replacement, *inputs.inventories[1:]))
    else:
        (bad.root / "unexpected").write_text("not recorded")
    with pytest.raises((ValueError, OSError)):
        await prepare_preview(inputs, spawn=never_spawn)
    assert untouched.is_dir()
    if fault in {"truncated", "bytes", "extra"}:
        assert not bad.root.exists()
    if fault == "identity":
        assert not next(i.root for i in inputs.inventories if i.role == "tool").exists()


@pytest.mark.parametrize(
    "payload", [b"unrecognized executable", b"MZfake PE", b"#!/outside/python\n"]
)
async def test_tool_formats_and_shebangs_refuse(inputs, payload):
    inventory = next(i for i in inputs.inventories if i.role == "tool")
    executable = inventory.root / "bin/python"
    executable.write_bytes(payload)
    updated = record(inventory.root, inventory.name, owned=True)
    inputs = replace(
        inputs,
        inventories=tuple(updated if i is inventory else i for i in inputs.inventories),
        tools=(
            replace(inputs.tools[0], sha256=hashlib.sha256(payload).hexdigest()),
            *inputs.tools[1:],
        ),
    )
    with pytest.raises(ValueError):
        await prepare_preview(inputs, spawn=never_spawn)


async def test_child_symlink_shebang_and_python_configuration_layout(inputs):
    tool = next(i for i in inputs.inventories if i.role == "tool")
    system = next(i for i in inputs.inventories if i.role == "system")
    (tool.root / "bin/uv-python").unlink()
    (tool.root / "bin/uv-python").symlink_to("python")
    payload = b"#!/deps/system/env node\nconsole.log('prepared')\n"
    (tool.root / "bin/pnpm").write_bytes(payload)
    (system.root / "env").write_bytes(static_elf())
    (system.root / "env").chmod(0o755)
    config = inputs.inventories[0].root / "pyvenv.cfg"
    config.write_text(f"home = {tool.root / 'bin'}\ninclude-system-site-packages = false\n")
    before = config.read_bytes()
    inventories = tuple(record(i.root, i.name, owned=i.owned) for i in inputs.inventories)
    tools = tuple(
        replace(t, sha256=hashlib.sha256(payload).hexdigest()) if t.name == "pnpm" else t
        for t in inputs.tools
    )
    prepared = await prepare_preview(
        replace(inputs, inventories=inventories, tools=tools), spawn=never_spawn
    )
    mapped = next(m for m in prepared.root_mappings if m.target == Path("/deps/backend"))
    assert (mapped.source / "pyvenv.cfg").read_text().startswith("home = /deps/tool/bin\n")
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["system", "empty", "mode", "hardlink", "config", "readonly", "supervisor"]
)
async def test_required_inputs_and_unsafe_layouts_refuse(inputs, fault):
    bad = inputs.inventories[0]
    if fault == "system":
        inputs = replace(inputs, system_entries=(Path("/deps/system/missing"),))
    elif fault == "empty":
        inputs = replace(
            inputs,
            inventories=(
                replace(bad, entries=(), digest=inventory_digest(())),
                *inputs.inventories[1:],
            ),
        )
    elif fault == "mode":
        (bad.root / "site/original.pth").chmod(0o777)
    elif fault == "hardlink":
        os.link(bad.root / "site/original.pth", inputs.preparation_storage / "unrelated-hardlink")
    elif fault == "config":
        (bad.root / "pyvenv.cfg").write_text(
            "home = /host/HOME/bin\ninclude-system-site-packages = true\n"
        )
        inputs = replace(
            inputs, inventories=(record(bad.root, bad.name, owned=True), *inputs.inventories[1:])
        )
    elif fault == "readonly":
        inputs = replace(
            inputs,
            root_mappings=(
                replace(inputs.root_mappings[0], read_only=False),
                *inputs.root_mappings[1:],
            ),
        )
    else:
        inputs = replace(
            inputs,
            root_mappings=(
                replace(inputs.root_mappings[0], target=Path("/state/logs")),
                *inputs.root_mappings[1:],
            ),
        )
    with pytest.raises((ValueError, OSError)):
        await prepare_preview(inputs, spawn=never_spawn)


async def test_effective_manifest_matches_private_environment(inputs):
    first = await prepare_preview(inputs, spawn=never_spawn)
    for inventory in first.manifests:
        assert (
            record(inventory.root, inventory.name, owned=inventory.owned).entries
            == inventory.entries
        )
    source = next(m.source for m in first.root_mappings if m.target == Path("/deps/backend"))
    (source / "site/original.pth").write_text("tampered output")
    second = await prepare_preview(inputs, spawn=never_spawn)
    new_source = next(m.source for m in second.root_mappings if m.target == Path("/deps/backend"))
    assert new_source != source
    assert "tampered" not in (new_source / "site/original.pth").read_text()


async def test_copy_cannot_mint_evidence_for_changed_dependency_bytes(inputs, monkeypatch):
    copy = shutil.copytree

    def corrupt(source, destination, *args, **kwargs):
        copied = copy(source, destination, *args, **kwargs)
        if Path(source) == inputs.inventories[0].root:
            (Path(destination) / "site/original.pth").write_text("changed during copy")
        return copied

    monkeypatch.setattr(shutil, "copytree", corrupt)
    with pytest.raises(ValueError, match="prepared content changed"):
        await prepare_preview(inputs, spawn=never_spawn)


@pytest.mark.parametrize("attack", ["unsafe-pth", "site-link", "T-link"])
async def test_import_mappings_refuse_unsupported_hooks_and_links(inputs, attack):
    inventory = inputs.inventories[0]
    if attack == "unsafe-pth":
        (inventory.root / "site/evil.pth").write_text("import arbitrary_code\n")
    elif attack == "site-link":
        (inventory.root / "site/evil.pth").symlink_to("/deps/system/entry")
    else:
        package = inputs.tree / "backend/src/backend_fixture.py"
        package.unlink()
        package.symlink_to(inputs.excluded_roots[0] / "outside.py")
        (inputs.excluded_roots[0] / "outside.py").write_text("VALUE = 'original'")
    updated = record(inventory.root, inventory.name, owned=True)
    inputs = replace(inputs, inventories=(updated, *inputs.inventories[1:]))
    with pytest.raises((ValueError, OSError)):
        await prepare_preview(inputs, spawn=never_spawn)


async def test_reuse_revalidates_and_evicts_only_owned_storage(inputs, tmp_path):
    await prepare_preview(inputs, spawn=never_spawn)
    await prepare_preview(inputs, spawn=never_spawn)
    inventory = inputs.inventories[0]
    (inventory.root / "site/original.pth").write_text("changed after first use")
    unrelated = inputs.preparation_storage / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    with pytest.raises(ValueError):
        await prepare_preview(inputs, spawn=never_spawn)
    assert not inventory.root.exists() and (unrelated / "keep").read_text() == "keep"
    assert inputs.inventories[1].root.exists()


async def test_prepared_imports_and_state_layout(inputs, monkeypatch):
    captured = []
    capture = preparation._effective_manifest

    def after_mountpoints(inventory, source):
        root = inputs.preview_directory / "root"
        targets = [m.target for m in inputs.root_mappings] + [
            Path("/state"),
            Path("/tmp"),
            Path("/proc"),
            Path("/dev"),
        ]
        assert all((root / target.relative_to("/")).is_dir() for target in targets)
        captured.append(inventory.name)
        return capture(inventory, source)

    monkeypatch.setattr(preparation, "_effective_manifest", after_mountpoints)
    before = {p: p.read_bytes() for p in inputs.preparation_storage.rglob("*") if p.is_file()}
    prepared = await prepare_preview(inputs, spawn=never_spawn)
    assert len(captured) == len(inputs.inventories)
    assert prepared.resources == inputs.resources
    assert prepared.build_log == inputs.preview_directory / "logs/build.log"
    assert prepared.state == inputs.preview_directory / "state"
    assert not prepared.state.is_relative_to(inputs.tree)
    for mapping in prepared.root_mappings:
        assert (prepared.root / mapping.target.relative_to("/")).exists()
        assert mapping.read_only or mapping.source in (inputs.tree, prepared.state)
        assert mapping.source not in (
            inputs.preview_directory,
            inputs.preview_directory / "logs",
            inputs.preview_directory / "metadata",
            inputs.preview_directory / "published",
        )
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not (inputs.excluded_roots[0] / "HOOK_RAN").exists()
    paths = []
    for mapped in prepared.import_paths:
        mount = next(m for m in prepared.root_mappings if mapped.is_relative_to(m.target))
        paths.append(str(mount.source / mapped.relative_to(mount.target)))
    result = await run_process(
        [
            sys.executable,
            "-S",
            "-c",
            "import site; "
            + "; ".join(f"site.addsitedir({path!r})" for path in paths if path.endswith("/site"))
            + "; import backend_fixture, contracts_fixture, demo_test_fixture; "
            "print(backend_fixture.__file__, contracts_fixture.__file__, "
            "demo_test_fixture.__file__)",
        ],
        cwd=inputs.tree,
        timeout_s=10,
        output_cap=4096,
        env={
            "PATH": "/nonexistent-approved-path",
            "PYTHONPATH": os.pathsep.join(paths),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 0 and not result.truncated and not result.timed_out
    assert result.stdout.count(str(inputs.tree)) == 3
    assert not (inputs.excluded_roots[0] / "HOOK_RAN").exists()
    assert not list(inputs.tree.rglob("__pycache__"))
    for mapping in prepared.root_mappings:
        if mapping.source in (inputs.tree, prepared.state):
            continue
        assert not list(mapping.source.rglob("__editable__*.py"))
        for path in mapping.source.rglob("*.pth"):
            assert (
                "import " not in path.read_text()
                and str(inputs.excluded_roots[0]) not in path.read_text()
            )


async def test_final_preview_environment(inputs, monkeypatch):
    prepared = await prepare_preview(inputs)
    monkeypatch.setenv("PATH", "/host/bin")
    monkeypatch.setenv("HOME", "/host/home")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/host")
    hostile = (
        dict(os.environ)
        | dict.fromkeys(
            (
                "META_API_KEY",
                "BASETEN_API_KEY",
                "AWS_SECRET_ACCESS_KEY",
                "HTTP_PROXY",
                "LD_PRELOAD",
                "PYTHONHOME",
                "PYTHONSTARTUP",
                "NODE_OPTIONS",
                "BASH_ENV",
                "GIT_CONFIG",
                "UV_PYTHON",
            ),
            "bad",
        )
        | {
            "UV_OFFLINE": "0",
            "UV_NO_SYNC": "0",
            "UV_PROJECT_ENVIRONMENT": "/original/.venv",
            "PYTHONPATH": "/host/imports",
            "LANG": "malicious",
            "TERM": "xterm",
        }
    )
    clean = scrub_preview_env(hostile, prepared)
    assert clean["UV_OFFLINE"] == clean["UV_NO_SYNC"] == "1"
    assert clean["UV_PROJECT_ENVIRONMENT"] == str(inputs.project_environment)
    assert clean["HOME"] == "/state/home" and clean["TMPDIR"] == "/tmp"
    assert clean["PATH"] == "/deps/tool/bin"
    assert clean["LANG"] == "C.UTF-8" and clean["TERM"] == "xterm"
    assert all(value != "bad" for value in clean.values())
    assert all("/host" not in value and "/original" not in value for value in clean.values())
    assert child_env(clean) == clean
    result = await run_process(
        [sys.executable, "-I", "-S", "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        cwd=inputs.tree,
        timeout_s=10,
        output_cap=8192,
        env=clean,
    )
    assert result.returncode == 0 and not result.timed_out and not result.truncated
    assert json.loads(result.stdout) == clean


async def test_frozen_inputs_and_unowned_mismatch(inputs):
    mutable = list(inputs.resources)
    frozen = replace(inputs, resources=mutable)
    mutable.clear()
    assert frozen.resources == inputs.resources
    with pytest.raises(FrozenInstanceError):
        frozen.tree = Path("/no")
    bad = replace(inputs.inventories[0], owned=False)
    (bad.root / "site/original.pth").write_text("mismatch")
    with pytest.raises(ValueError):
        await prepare_preview(replace(inputs, inventories=(bad, *inputs.inventories[1:])))
    assert bad.root.exists()
