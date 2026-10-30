"""Run only disposable trees; hidden artifacts never enter the supplied workspace."""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = Path("/home/matidegli/Projects/oversight/.runtime/demo-venv/bin/python")
TIMEOUT = 120
IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", "_proof2_checks"}


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    issue: int | None
    module: str
    prompt: str
    hidden_checks: list[str]
    reference: str | None
    after: list[str]
    pairs: list[str]
    mutants: list[str] = field(default_factory=list)


def resource(name: str) -> Path:
    path = (HERE / name).resolve()
    if not path.is_relative_to(HERE) or not path.is_file():
        raise ValueError(f"invalid resource: {name}")
    return path


def load_tasks() -> list[Task]:
    tasks = [Task(**item) for item in json.loads((HERE / "tasks.json").read_text())]
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("duplicate task ids")
    for task in tasks:
        for name in [
            *task.hidden_checks,
            *task.mutants,
            *([task.reference] if task.reference else []),
        ]:
            resource(name)
    return tasks


def source_tree(tree: Path) -> Path:
    tree = tree.resolve()
    if not tree.is_dir() or not (tree / "boltons" / "__init__.py").is_file():
        raise ValueError(f"missing Boltons tree: {tree}")
    return tree


def ignored(_directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in IGNORED or n.startswith((".env", ".proof2-"))}


def copy_tree(tree: Path, target: Path) -> None:
    # Preserve links without following them, then reject them before executing anything.
    shutil.copytree(tree, target, ignore=ignored, symlinks=True)
    if any(p.is_symlink() for p in target.rglob("*")):
        raise ValueError("symlinks are not allowed in a check tree")


async def process(argv: list[str], root: Path, timeout_s: float) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTEST_"))}
    for key in ("META_API_KEY", "BASETEN_API_KEY", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env.update(
        PYTHONPATH=str(root),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CEILING_DIRECTORIES=str(root.parent),
    )
    child = await asyncio.create_subprocess_exec(
        *argv,
        cwd=root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(child.communicate(), timeout_s)
        return child.returncode or 0, output.decode(errors="replace")
    except TimeoutError:
        return 124, "process timed out"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        await asyncio.wait_for(child.wait(), 5)


async def suite(root: Path, targets: list[str], timeout_s: float = TIMEOUT) -> dict:
    report = root.parent / "report.xml"
    report.unlink(missing_ok=True)
    config = root / "pyproject.toml"
    if not config.exists():
        config = root / ".proof2-pytest.ini"
        config.write_text("[pytest]\n")
    start = time.monotonic()
    code, output = await process(
        [
            str(PYTHON),
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-c",
            str(config),
            "--rootdir",
            str(root),
            "--confcutdir",
            str(root),
            f"--junitxml={report}",
            *targets,
        ],
        root,
        timeout_s,
    )
    result: dict = dict(
        passed=0,
        failed=0,
        errors=0,
        duration=round(time.monotonic() - start, 3),
        failing_test_ids=[],
        exit_code=code,
    )
    if report.exists():
        for case in ET.parse(report).iter("testcase"):
            failure = case.find("failure") is not None
            error = case.find("error") is not None
            if failure or error:
                result["failed" if failure else "errors"] += 1
                result["failing_test_ids"].append(
                    case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
                )
            elif case.find("skipped") is None:
                result["passed"] += 1
    if code and not result["failed"] and not result["errors"]:
        result["errors"] = 1
    if result["errors"]:
        result["output"] = output[-12000:]
    return result


def install_checks(root: Path, task: Task) -> list[str]:
    hidden = root / "_proof2_checks"
    hidden.mkdir()
    targets = []
    for name in task.hidden_checks:
        target = hidden / Path(name).name
        shutil.copyfile(resource(name), target)
        targets.append(str(target.relative_to(root)))
    return targets


def files(root: Path) -> dict[str, bytes]:
    result = {}
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = [n for n in names if n != ".git"]
        for name in [*names, *filenames]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("symlinks are not allowed in a check tree")
            if name.startswith(".env"):
                raise ValueError("environment files cannot be compared")
        for name in filenames:
            if name != ".git":
                path = Path(directory) / name
                result[str(path.relative_to(root))] = path.read_bytes()
    return result


async def apply_mutant(root: Path, task: Task, name: str) -> None:
    override = resource(name).read_text()
    header = override.splitlines()[0]
    if not header.startswith("# targets: "):
        raise ValueError(f"{name}: missing target declaration")
    targets = header.removeprefix("# targets: ").split(", ")
    # Resolve every target before appending anything; import errors are refusals, not kills.
    probe = """import importlib, sys
module = importlib.import_module('boltons.' + sys.argv[1])
for target in sys.argv[2:]:
    obj = module
    for part in target.split('.'):
        obj = getattr(obj, part)
"""
    code, output = await process([str(PYTHON), "-c", probe, task.module, *targets], root, 30)
    if code:
        raise ValueError(f"{name}: target unavailable ({', '.join(targets)}): {output}")
    module = root / "boltons" / f"{task.module}.py"
    module.write_text(module.read_text() + "\n\n" + override)


async def mutant_results(
    task: Task, tree: Path, targets: list[str], timeout_s: float
) -> list[dict]:
    results = []
    if len(task.mutants) != 3 or len(set(task.mutants)) != 3:
        raise ValueError(f"{task.id}: expected three distinct mutants")
    for name in task.mutants:
        with tempfile.TemporaryDirectory(prefix=".proof2-", dir=HERE) as directory:
            root = Path(directory) / "tree"
            copy_tree(tree, root)
            try:
                await apply_mutant(root, task, name)
            except ValueError as error:
                results.append(
                    dict(id=Path(name).stem, applies=False, killed=False, reason=str(error))
                )
                continue
            result = await suite(root, targets, timeout_s)
        killed = result["exit_code"] in (1, 2) and result["failed"] + result["errors"] > 0
        results.append(dict(id=Path(name).stem, applies=True, killed=killed, **result))
    return results


async def check_tests(task: Task, tree: Path, base: Path, timeout_s: float) -> dict:
    result: dict = dict(
        task=task.id,
        tree=str(tree),
        base=str(base),
        production_unchanged=False,
        new_tests_present=False,
        new_tests_pass=False,
        mutation_threshold=False,
        new_test_files=[],
        production_changed_paths=[],
        killed_mutants=[],
        mutants=[],
        exit_code=1,
    )
    with tempfile.TemporaryDirectory(prefix=".proof2-", dir=HERE) as directory:
        root = Path(directory) / "tree"
        current, previous = files(tree), files(base)
        copy_tree(tree, root)
        changed = sorted(
            k for k in current.keys() | previous.keys() if current.get(k) != previous.get(k)
        )
        production = [k for k in changed if not k.startswith("tests/")]
        tests = [
            k
            for k in changed
            if k.startswith("tests/")
            and k.endswith(".py")
            and k in current
            and (Path(k).name.startswith("test_") or Path(k).name.endswith("_test.py"))
        ]
        result.update(
            production_unchanged=not production,
            production_changed_paths=production,
            new_tests_present=bool(tests),
            new_test_files=tests,
        )
        if production or not tests:
            result["reason"] = "production code changed" if production else "no new tests"
            return result
        observed = await suite(root, tests, timeout_s)
        result.update(
            tests=observed, new_tests_pass=observed["exit_code"] == 0 and observed["passed"] > 0
        )
    if not result["new_tests_pass"]:
        result["reason"] = "new tests fail on base"
        return result
    mutants = await mutant_results(task, tree, tests, timeout_s)
    killed = [m["id"] for m in mutants if m["killed"]]
    result.update(
        mutants=mutants,
        killed_mutants=killed,
        mutation_threshold=len(killed) >= 2,
        exit_code=0 if len(killed) >= 2 else 1,
    )
    if result["exit_code"]:
        result["reason"] = "fewer than two mutants killed"
    return result


async def check(
    task_id: str, tree: Path, *, base: Path | None = None, timeout_s: float = TIMEOUT
) -> dict:
    task = next((t for t in load_tasks() if t.id == task_id), None)
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    if task.kind == "tests":
        if base is None:
            raise ValueError(f"{task_id}: --base is required for tests-only tasks")
        return await check_tests(task, source_tree(tree), source_tree(base), timeout_s)
    if base is not None:
        raise ValueError(f"{task_id}: --base is only allowed for tests-only tasks")
    if not task.hidden_checks:
        raise ValueError(f"{task_id}: no hidden checks")
    tree = source_tree(tree)
    with tempfile.TemporaryDirectory(prefix=".proof2-", dir=HERE) as directory:
        root = Path(directory) / "tree"
        copy_tree(tree, root)
        result = await suite(root, install_checks(root, task), timeout_s)
    return dict(task=task.id, tree=str(tree), **result)


async def validate(source: Path) -> list[dict]:
    source = source_tree(source)
    results = []
    for task in load_tasks():
        if task.kind == "tests":
            result = await validate_tests(task, source)
            results.append(result)
            print(json.dumps(result), flush=True)
            continue
        if not task.hidden_checks:
            continue
        base = await check(task.id, source)
        with tempfile.TemporaryDirectory(prefix=".proof2-", dir=HERE) as directory:
            root = Path(directory) / "tree"
            copy_tree(source, root)
            if not task.reference:
                raise ValueError(f"{task.id}: no reference")
            code, output = await process(
                ["git", "apply", "--", str(resource(task.reference))], root, 30
            )
            if code:
                raise ValueError(f"{task.id}: reference patch failed: {output}")
            reference = await suite(root, install_checks(root, task))
            original = await suite(root, ["--doctest-modules", "boltons", "tests"])
        # Each hidden file must expose a failure; collection/setup errors are not evidence.
        base_fails = (
            base["exit_code"] == 1
            and base["errors"] == 0
            and all(
                any(f"/{Path(name).name}::" in node for node in base["failing_test_ids"])
                for name in task.hidden_checks
            )
        )
        ok = base_fails and reference["exit_code"] == 0 and original["exit_code"] == 0
        result = dict(
            task=task.id,
            base_fails=base_fails,
            reference_passes=reference["exit_code"] == 0,
            original_passes=original["exit_code"] == 0,
            ok=ok,
            base=base,
            reference=reference,
            original=original,
        )
        results.append(result)
        print(json.dumps(result), flush=True)
    return results


async def validate_tests(task: Task, source: Path) -> dict:
    if not task.reference:
        raise ValueError(f"{task.id}: no reference test")
    with tempfile.TemporaryDirectory(prefix=".proof2-", dir=HERE) as directory:
        root, baseline = Path(directory) / "tree", Path(directory) / "base"
        copy_tree(source, baseline)
        copy_tree(baseline, root)
        target = root / "tests" / ("test_proof2_" + task.id + ".py")
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(resource(task.reference), target)
        reference = await check(task.id, root, base=baseline)
    original = await mutant_results(
        task, source, ["--doctest-modules", "boltons", "tests"], TIMEOUT
    )
    applies = len(reference["mutants"]) == len(original) == 3 and all(
        m["applies"] for m in [*reference["mutants"], *original]
    )
    ok = applies and reference["exit_code"] == 0 and len(reference["killed_mutants"]) == 3
    return dict(
        task=task.id,
        reference_passes=reference["new_tests_pass"],
        all_mutants_apply=applies,
        reference_kills=reference["killed_mutants"],
        original_kills=[m["id"] for m in original if m["killed"]],
        reference=reference,
        original=original,
        ok=ok,
    )
