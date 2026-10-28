"""Frontend dependency readers must wait for one offline, frozen install under parallel make."""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def make_rules() -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Read the explicit rules, joining continuations and ignoring target-specific variables."""
    dependencies: dict[str, set[str]] = {}
    recipes: dict[str, list[str]] = {}
    targets: list[str] = []
    for line in (ROOT / "Makefile").read_text().replace("\\\n", " ").splitlines():
        if line.startswith("\t"):
            for target in targets:
                recipes[target].append(line)
        elif line and not line.startswith("#"):
            rule = re.fullmatch(r"([a-z][\w-]*(?: [a-z][\w-]*)*):([^:=]*)", line.split("#")[0])
            targets = rule[1].split() if rule else []
            for target in targets:
                dependencies.setdefault(target, set()).update(rule[2].split() if rule else [])
                recipes.setdefault(target, [])
    return dependencies, recipes


def reachable(target: str, dependencies: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    pending = list(dependencies.get(target, set()))
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            pending.extend(dependencies.get(name, set()))
    return found


def test_every_frontend_reader_in_the_check_graph_waits_for_frontend_deps() -> None:
    dependencies, recipes = make_rules()
    gates = re.search(r"^GATES := (.+)$", (ROOT / "Makefile").read_text(), re.MULTILINE)
    assert gates is not None
    # check/check-ci invoke the same gates through recursive make; check-fast has normal edges.
    dependencies["check"] = set(gates[1].split())
    dependencies["check-ci"] = dependencies["check"] - {"check-secrets"}
    graph = set().union(
        *(reachable(root, dependencies) for root in ("check", "check-ci", "check-fast"))
    )
    readers = {
        target
        for target in graph
        if target != "frontend-deps"
        and any(
            "$(PNPM)" in line or ("frontend" in line and re.search(r"\b(node|pnpm)\b", line))
            for line in recipes.get(target, [])
        )
    }
    assert {"lint-frontend", "check-frontend", "check-contract-tuples"} <= readers
    # Indirect readers: pytest runs TS/schema controls, generate.py invokes json2ts, and
    # notices.py inspects the installed lock and invokes pnpm licenses/list.
    readers |= {"check-python", "check-contracts", "check-notices"}
    for target in sorted(readers | {"check", "check-ci", "check-fast"}):
        assert "frontend-deps" in reachable(target, dependencies), target


@pytest.mark.parametrize("target", ["check", "check-ci", "check-fast"])
def test_each_check_entry_point_installs_once_offline_and_frozen(target: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
    done = subprocess.run(
        ["make", "-n", "--no-print-directory", "-j4", target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    installs = [line for line in done.stdout.splitlines() if "pnpm -C frontend install" in line]
    assert len(installs) == 1, done.stdout
    assert "--frozen-lockfile" in installs[0] and "--offline" in installs[0]
