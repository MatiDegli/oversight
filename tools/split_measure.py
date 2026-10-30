"""Offline split measurement. Run with uv run --offline python tools/split_measure.py --help.

Requires an already prepared, clean pinned Boltons checkout; never fetches or runs code
from either measured repository. Outputs report.json and summary.md. No server or model.
"""

import argparse
import ast
import asyncio
import difflib
import hashlib
import itertools
import json
import random
import re
import tokenize
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oversight.changes import CannotSplit, partition_targets
from oversight.graph import WorkspaceGraph, graph_completeness
from oversight.workspace import BOLTONS, SOURCE_GIT_ENV, run_process
from oversight_contracts.graph import GraphSnapshot

SEED = 20261030
REPO = Path(__file__).resolve().parents[1]
type Lines = dict[str, set[int]]
type Report = dict[str, Any]


@dataclass(frozen=True)
class Task:
    id: str
    targets: tuple[str, ...]
    changed_lines: dict[str, list[int]]
    reference: str


def _added_definitions(text: str, first: int, last: int) -> set[int]:
    """New definitions have no base-side identity, including their decorators and body."""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not first <= node.lineno <= last:
                continue
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            lines.update(range(start, (node.end_lineno or node.lineno) + 1))
    return lines


def changed_lines(before: str, after: str) -> set[int]:
    """Deleted/replaced base lines; an insertion maps only inside an existing span.

    Pure insertions use both surrounding lines, later requiring the same innermost
    function on both sides. Inserted definitions and blank separators map to nothing.
    Negative numbers mark the second side of an insertion boundary.
    """
    old, new = before.splitlines(), after.splitlines()
    result: set[int] = set()
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if a < b:
            result.update(range(a + 1, b + 1))
        else:
            definitions = _added_definitions(after, c + 1, d)
            if any(new[i].strip() and i + 1 not in definitions for i in range(c, d)):
                result.add(-(a + 1))
    return result


def map_targets(graph: WorkspaceGraph, snapshot: GraphSnapshot, changes: Lines) -> Task:
    """Resolve through the actual span index, retaining function/method identities only."""
    functions = {n.id for n in snapshot.nodes if n.kind in ("function", "method")}
    targets: set[str] = set()
    audit: dict[str, list[int]] = {}
    for path, lines in sorted(changes.items()):
        accepted = {line for line in lines if line > 0}
        for marker in (line for line in lines if line < 0):
            right = -marker
            if graph.node_at(path, right - 1) == graph.node_at(path, right):
                accepted.update((right - 1, right))
        audit[path] = sorted(accepted)
        targets.update(
            node
            for line in accepted
            if (node := graph.node_at(path, line)) is not None and node in functions
        )
    return Task("", tuple(sorted(targets)), audit, "")


def patch_changes(root: Path, patch: str, unavailable: frozenset[str] = frozenset()) -> Lines:
    """Apply unified hunks in memory, verifying every context/deletion and hunk count."""
    changes: Lines = {}
    path: str | None = None
    lines = patch.splitlines(keepends=True)
    cursor = 0
    while cursor < len(lines):
        line = lines[cursor]
        if line.startswith("--- "):
            raw = line[4:].strip().removeprefix("a/")
            path = None if raw == "/dev/null" else raw
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        cursor += 1
        if match is None:
            continue
        start, old_count, _, new_count = match.groups()
        old_count, new_count = int(old_count or 1), int(new_count or 1)
        old: list[str] = []
        new: list[str] = []
        while cursor < len(lines) and (len(old) < old_count or len(new) < new_count):
            item = lines[cursor]
            cursor += 1
            if item.startswith("\\ No newline"):
                continue
            if item[:1] not in (" ", "-", "+"):
                raise ValueError("malformed reference hunk")
            if item[0] != "+":
                old.append(item[1:])
            if item[0] != "-":
                new.append(item[1:])
        if len(old) != old_count or len(new) != new_count:
            raise ValueError("reference hunk count mismatch")
        if path is None or path in unavailable:
            continue  # New or unparsed file: no base-side nodes.
        base = _source_text(root, path).splitlines(keepends=True)
        offset = int(start) - 1 if old_count else int(start)
        if base[offset : offset + old_count] != old:
            raise ValueError(f"reference context mismatch: {path}:{start}")
        # Use the full file so inserted definitions can be distinguished from edits
        # inside an existing function; the original hunk offsets remain base-side.
        modified = base[:offset] + new + base[offset + old_count :]
        changes.setdefault(path, set()).update(changed_lines("".join(base), "".join(modified)))
    return changes


def _source_path(root: Path, path: str) -> Path:
    full = root / path
    if full.resolve() != full.absolute() or not full.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe input path: {path}")
    return full


def _source_text(root: Path, path: str) -> str:
    with tokenize.open(_source_path(root, path)) as handle:
        return handle.read()


def _edits(text: str, edits: list[Report]) -> str:
    for edit in edits:
        if text.count(edit["old"]) != 1:
            raise ValueError("reference fixture must match exactly once")
        text = text.replace(edit["old"], edit["new"], 1)
    return text


def fixture_changes(
    root: Path, task: Report, data_dir: Path, unavailable: frozenset[str] = frozenset()
) -> Lines:
    """References apply to seeded bases. Project changed lines back to the scanned base."""
    reference = json.loads((data_dir / task["reference_solution"]).read_text())
    seeds: list[Report] = (
        json.loads((data_dir / task["seeded_defect"]).read_text())
        if task.get("seeded_defect")
        else []
    )
    result: Lines = {}
    for path in sorted({e["path"] for e in reference}):
        if path in unavailable:
            continue
        original = _source_text(root, path)
        seeded = _edits(original, [e for e in seeds if e["path"] == path])
        fixed = _edits(seeded, [e for e in reference if e["path"] == path])
        affected = changed_lines(seeded, fixed)
        projected: set[int] = set()
        for tag, a, b, c, d in difflib.SequenceMatcher(
            None, original.splitlines(), seeded.splitlines(), autojunk=False
        ).get_opcodes():
            for line in affected:
                if c < line <= d:
                    projected.update([a + line - c] if tag == "equal" else range(a + 1, b + 1))
                elif line < 0 and c < -line <= d and tag == "equal":
                    projected.add(-(a - line - c))
        result[path] = projected
    return result


def load_tasks(
    root: Path,
    graph: WorkspaceGraph,
    snapshot: GraphSnapshot,
    tasks_file: Path,
    unavailable: frozenset[str] = frozenset(),
) -> tuple[list[Task], dict[str, str]]:
    tasks: list[Task] = []
    hashes = {str(tasks_file.relative_to(REPO)): _hash(tasks_file)}
    for task in json.loads(tasks_file.read_text()):
        reference = tasks_file.parent / task.get("reference", task.get("reference_solution"))
        for input_path in [reference] + (
            [tasks_file.parent / task["seeded_defect"]] if task.get("seeded_defect") else []
        ):
            hashes[str(input_path.relative_to(REPO))] = _hash(input_path)
        if reference.suffix == ".patch":
            lines = patch_changes(root, reference.read_text(), unavailable)
        elif reference.suffix == ".json":
            lines = fixture_changes(root, task, tasks_file.parent, unavailable)
        else:
            # Proof 2's standalone reference tests are additions, never production targets.
            lines = {}
        mapped = map_targets(graph, snapshot, lines)
        tasks.append(Task(task["id"], mapped.targets, mapped.changed_lines, reference.name))
    return tasks, hashes


def import_links(root: Path, snapshot: GraphSnapshot) -> set[tuple[str, str]]:
    """Module paths coupled by imports, including relative/package imports in the fallback."""
    nodes = {n.id: n for n in snapshot.nodes}
    links = {(nodes[e.src].path, nodes[e.dst].path) for e in snapshot.edges if e.kind == "imports"}
    if links:
        return links
    modules = {n.id: n.path for n in snapshot.nodes if n.kind == "module"}
    for module, path in modules.items():
        package = module.split(".") if path.endswith("/__init__.py") else module.split(".")[:-1]
        for node in ast.walk(ast.parse(_source_path(root, path).read_bytes())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > len(package):
                    continue
                prefix = package[: len(package) - node.level + 1] if node.level else []
                base = ".".join(prefix + ([node.module] if node.module else []))
                names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for name in names:
                if name in modules:
                    links.add((path, modules[name]))
    return links


def sample_pairs(snapshot: GraphSnapshot, count: int = 1000) -> list[tuple[Task, Task]]:
    """Uniform unique unordered pairs, without materializing O(n²) candidates."""
    ids = sorted(n.id for n in snapshot.nodes if n.kind in ("function", "method") and not n.is_test)
    total = len(ids) * (len(ids) - 1) // 2
    ranks = sorted(random.Random(SEED).sample(range(total), min(count, total)))
    tasks = {node: Task(node, (node,), {}, "sample") for node in ids}
    pairs: list[tuple[Task, Task]] = []
    row, start = 0, 0
    for rank in ranks:
        while rank >= start + len(ids) - row - 1:
            start += len(ids) - row - 1
            row += 1
        pairs.append((tasks[ids[row]], tasks[ids[row + 1 + rank - start]]))
    return pairs


def evaluate_pair(
    snapshot: GraphSnapshot,
    pair: tuple[Task, Task],
    *,
    complete: bool,
    imports: set[tuple[str, str]],
    variant: str,
) -> Report:
    result = partition_targets(
        snapshot,
        pair[0].targets + pair[1].targets,
        completeness="complete" if complete else "incomplete",
    )
    record: Report = {
        "tasks": [t.id for t in pair],
        "verdict": "no split possible",
        "diagnostics": [],
    }
    if isinstance(result, CannotSplit):
        record["diagnostics"] = [asdict(d) for d in result.diagnostics]
        return record
    zones = [set(zone) for zone in result.zones]
    targets = [set(task.targets) for task in pair]
    if not any(
        targets[i] <= zones[0] and targets[1 - i] <= zones[1] and not targets[0] & targets[1]
        for i in (0, 1)
    ):
        record["diagnostics"] = [{"code": "task_targets_mixed", "ids": []}]
        return record
    paths = [{n.path for n in snapshot.nodes if n.id in zone} for zone in zones]
    conflicts = sorted(
        (a, b)
        for a, b in imports
        if (a in paths[0] and b in paths[1]) or (a in paths[1] and b in paths[0])
    )
    if variant == "call-plus-import" and conflicts:
        record["diagnostics"] = [
            {"code": "import_coupling", "ids": list(link)} for link in conflicts
        ]
        return record
    record.update(verdict="split", zones=result.zones)
    return record


def measure_repository(root: Path, commit: str, tasks_file: Path, *, sample: bool) -> Report:
    graph = WorkspaceGraph(root)
    snapshot = graph.scan(commit)
    completeness = graph_completeness(root)
    gaps = [f.path for f in completeness if f.status not in ("parsed", "unimportable")]
    scanned_version = hashlib.sha256(
        "".join(f"{f.path}\0{f.sha256}\n" for f in completeness if f.status == "parsed").encode()
    ).hexdigest()
    if not gaps and scanned_version != snapshot.version:
        raise ValueError("source changed during measurement; rerun on an unchanged checkout")
    tasks, hashes = load_tasks(root, graph, snapshot, tasks_file, frozenset(gaps))
    eligible = [t for t in tasks if t.targets]
    pairs = list(itertools.combinations(eligible, 2))
    groups = {"tasks": pairs}
    if sample:
        groups["sample"] = sample_pairs(snapshot)
    unimportable = [f.path for f in completeness if f.status == "unimportable"]
    imports = import_links(root, snapshot) if not gaps else set[tuple[str, str]]()
    policies: Report = {}
    for policy, counted in (
        ("count_unimportable", gaps + unimportable),
        ("ignore_unimportable", gaps),
    ):
        variants: Report = {}
        for variant in ("call-only", "call-plus-import"):
            measurements: Report = {}
            for name, candidates in groups.items():
                records = [
                    evaluate_pair(
                        snapshot, pair, complete=not counted, imports=imports, variant=variant
                    )
                    for pair in candidates
                ]
                split = sum(r["verdict"] == "split" for r in records)
                histogram = Counter(d["code"] for r in records for d in r["diagnostics"])
                measurements[name] = {
                    "split": split,
                    "denominator": len(records),
                    "share": split / len(records) if records else None,
                    "refusals": dict(sorted(histogram.items())),
                    "pairs": records,
                }
            variants[variant] = measurements
        policies[policy] = {
            "complete": not counted,
            "gap_files": sorted(counted),
            "variants": variants,
        }
    return {
        "commit": commit,
        "root": str(root),
        "snapshot_sha256": hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest(),
        "files": [asdict(f) for f in completeness],
        "reference_hashes": hashes,
        "targets": [asdict(t) | {"excluded": not t.targets} for t in tasks],
        "nominal_task_pairs": len(tasks) * (len(tasks) - 1) // 2,
        "excluded_task_pairs": len(tasks) * (len(tasks) - 1) // 2 - len(pairs),
        "unimportable_files": unimportable,
        "policies": policies,
    }


def build_report(boltons: Report, demo: Report, tool_commit: str) -> Report:
    rules: Report = {}
    for policy in ("count_unimportable", "ignore_unimportable"):
        b = boltons["policies"][policy]["variants"]["call-plus-import"]["tasks"]
        d = demo["policies"][policy]["variants"]["call-plus-import"]["tasks"]
        rules[policy] = bool(
            b["denominator"] and b["split"] * 5 >= b["denominator"] and d["split"] >= 1
        )
    return {
        "seed": SEED,
        "tool_commit": tool_commit,
        "tool_sha256": _hash(Path(__file__)),
        "suggested_rule": {
            "label": "suggested rule, pending the maintainer's approval",
            "text": (
                "At least 1 in 5 Boltons task pairs split under call-plus-import, "
                "and at least 1 demo pair splits."
            ),
            "denominator": "Eligible task pairs only; no-target tasks are excluded.",
            "passes": rules,
        },
        "repositories": {"boltons": boltons, "demo": demo},
    }


def markdown(report: Report) -> str:
    lines = [
        "# Free split measurement",
        "",
        f"Seed: {report['seed']}",
        "",
        "Suggested rule, pending the maintainer's approval.",
        report["suggested_rule"]["text"],
        "",
        f"Rule results: `{json.dumps(report['suggested_rule']['passes'], sort_keys=True)}`.",
        "",
        "Denominators exclude tasks without targets. "
        "Refusal histograms count diagnostics, not pairs.",
    ]
    for name, repo in report["repositories"].items():
        lines += [
            "",
            f"## {name} ({repo['commit']})",
            "",
            f"Nominal task pairs: {repo['nominal_task_pairs']}; "
            f"excluded: {repo['excluded_task_pairs']}.",
            "",
            "| Policy | Complete | Gaps |",
            "| --- | --- | --- |",
        ]
        for policy, result in repo["policies"].items():
            lines.append(
                f"| {policy} | {result['complete']} | {', '.join(result['gap_files']) or 'none'} |"
            )
        lines += [
            "",
            "| Policy | Variant | Pairs | Split / denominator | Share | Refusals |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for policy, result in repo["policies"].items():
            for variant, groups in result["variants"].items():
                for group, counts in groups.items():
                    share = f"{counts['share']:.1%}" if counts["share"] is not None else "n/a"
                    lines.append(
                        f"| {policy} | {variant} | {group} | "
                        f"{counts['split']}/{counts['denominator']} | {share} | "
                        f"{json.dumps(counts['refusals'], sort_keys=True)} |"
                    )
        lines += ["", "| Task | Targets | Base lines |", "| --- | --- | --- |"]
        for task in repo["targets"]:
            lines.append(
                f"| {task['id']} | {', '.join(task['targets']) or 'none (excluded)'} | "
                f"{json.dumps(task['changed_lines'], sort_keys=True)} |"
            )
    return "\n".join(lines) + "\n"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _git(root: Path, *args: str) -> str:
    result = await run_process(["git", *args], cwd=root, timeout_s=30, env=SOURCE_GIT_ENV)
    if result.returncode != 0 or result.timed_out or result.truncated:
        raise ValueError(f"cannot verify input checkout: {root}")
    return result.stdout.strip()


async def run(args: argparse.Namespace) -> None:
    root = args.boltons_root.resolve()
    commit = await _git(root, "rev-parse", "HEAD")
    if commit != BOLTONS.commit or await _git(
        root, "status", "--porcelain", "--untracked-files=all"
    ):
        raise ValueError(
            "Boltons must be a clean checkout of the pinned commit; this tool never fetches"
        )
    own_commit = await _git(REPO, "rev-parse", "HEAD")
    boltons = await asyncio.to_thread(
        measure_repository, root, commit, REPO / "tools/eval/proof2/tasks.json", sample=True
    )
    demo = await asyncio.to_thread(
        measure_repository,
        args.demo_root.resolve(),
        await _git(args.demo_root.resolve(), "rev-parse", "HEAD"),
        REPO / "tools/eval/tasks.json",
        sample=False,
    )
    report = build_report(boltons, demo, own_commit)
    summary = markdown(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "summary.md").write_text(summary)
    print(summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boltons-root", type=Path, required=True, help="Existing pinned checkout")
    parser.add_argument("--demo-root", type=Path, default=REPO / "demo-repo")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError, SyntaxError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
