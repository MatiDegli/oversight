"""Initial context is pure, deterministic, source-grounded and globally bounded."""

import ast
import random
from pathlib import Path
from textwrap import dedent

import pytest

from oversight.changes import _initial_context
from oversight.changes._initial_context import (
    BYTES_PER_TOKEN,
    CONSTANT_CHARS,
    SIGNATURE_CHARS,
    InitialContext,
    initial_context,
)
from oversight.changes._neighbors import LIMIT
from oversight.graph import parse_version
from oversight_contracts.graph import GraphSnapshot

HEADING = (
    "Context for your task, built from the repository's graph and source; not a complete list."
)


def graph(files: dict[str, str]) -> GraphSnapshot:
    return parse_version({p: s.encode() for p, s in files.items()}, "c1").graph


@pytest.fixture
def example() -> tuple[dict[str, str], GraphSnapshot, list[str]]:
    files = {
        "mod.py": dedent("""\
            RATE = 2
            def deco(fn): return fn
            class Runner:
                @deco
                def alpha(self, x: int = 3) -> int:
                    return beta(x) * RATE
                def caller(self):
                    return self.alpha()
            def beta(x=1):
                return x
            def uncertain(obj):
                return obj.alpha()
        """)
    }
    return files, graph(files), ["mod:Runner.alpha", "mod:beta"]


def test_full_text_golden(example: tuple[dict[str, str], GraphSnapshot, list[str]]) -> None:
    files, snapshot, targets = example
    result = initial_context(snapshot, files, targets)
    assert result.text == (
        f"{HEADING}\nRunner.alpha (mod.py)\n@deco\n"
        "def alpha(self, x: int=3) -> int\n"
        "Callers: Runner.caller (mod.py; exact), uncertain (mod.py; possible)\n"
        "Callees: beta (mod.py; exact)\nConstants: RATE = 2\n\n"
        "beta (mod.py)\ndef beta(x=1)\nCallers: Runner.alpha (mod.py; exact)"
    )
    assert result.omitted == ()
    assert result.tokens == (len(result.text.encode()) + 2) // BYTES_PER_TOKEN


def test_budget_sweep_and_signature_priority(
    example: tuple[dict[str, str], GraphSnapshot, list[str]],
) -> None:
    files, snapshot, targets = example
    signature_only = []
    for budget in range(200):
        result = initial_context(snapshot, files, targets, budget=budget)
        assert result.tokens <= budget
        assert result.tokens == (len(result.text.encode()) + 2) // BYTES_PER_TOKEN
        if all(f"def {name}(" in result.text for name in ("alpha", "beta")) and (
            "Callers:" not in result.text
        ):
            signature_only.append(result)
    assert signature_only
    assert signature_only[0].text.endswith("Omitted: 3 callers, 1 callees, 1 constants")
    assert len(signature_only[0].omitted) == 5
    for budget, omitted in (
        (100, "1 callers, 1 callees, 1 constants"),
        (106, "1 callers, 1 constants"),
        (109, "1 callers"),
    ):
        result = initial_context(snapshot, files, targets, budget=budget)
        assert "Callers: Runner.caller (mod.py; exact)" in result.text
        assert "Callers: Runner.alpha (mod.py; exact)" in result.text
        assert result.text.endswith(f"Omitted: {omitted}")
    assert initial_context(snapshot, files, targets, budget=0).text == ""
    with pytest.raises(ValueError, match="non-negative"):
        initial_context(snapshot, files, targets, budget=-1)


def test_skips_large_entry_and_keeps_later_small_entry() -> None:
    long_name = "a" * 150
    files = {"mod.py": f"def target(): pass\ndef {long_name}(): target()\ndef z(): target()"}
    snapshot = graph(files)
    results = [initial_context(snapshot, files, ["mod:target"], budget=b) for b in range(150)]
    result = next(r for r in results if "Callers: z (" in r.text and long_name not in r.text)
    assert result.text.endswith("Omitted: 1 callers")
    assert result.omitted == (f"caller mod:{long_name}: budget",)


def test_possible_callees_are_labeled_and_exact_neighbors_win_the_limit() -> None:
    files = {"mod.py": "class C:\n    def target(self, obj): obj.peer()\n    def peer(self): pass"}
    snapshot = graph(files)
    result = initial_context(snapshot, files, ["mod:C.target"])
    assert "Callees: C.peer (mod.py; possible)" in result.text
    files["mod.py"] += "\n" + "\n".join(
        f"def caller{i}(obj): obj.target(obj)" for i in range(LIMIT + 1)
    )
    snapshot = graph(files)
    edge = next(e for e in snapshot.edges if e.src == f"mod:caller{LIMIT}")
    exact = edge.model_copy(update={"confidence": "exact", "reason": None})
    snapshot = snapshot.model_copy(update={"edges": [*snapshot.edges, exact]})
    result = initial_context(snapshot, files, ["mod:C.target"])
    assert f"Callers: caller{LIMIT} (mod.py; exact), caller0 (mod.py; possible)" in result.text
    assert result.omitted == (f"caller mod:caller{LIMIT - 1}: list limit",)


def test_constants_only_used_literal_single_module_bindings() -> None:
    files = {
        "mod.py": dedent(f"""\
            from other import IMPORTED
            USED: int = 2
            UNUSED = 3
            lower = 4
            TWICE = 1
            TWICE = 2
            PARAM = 1
            LOCAL = 2
            LONG = {"x" * (CONSTANT_CHARS + 1)!r}
            DYNAMIC = object()
            CHAIN = OTHER = 3
            def target(PARAM):
                LOCAL = 4
                return USED, lower, TWICE, IMPORTED, PARAM, LOCAL, LONG, DYNAMIC, CHAIN
        """),
        "other.py": "IMPORTED = 6",
    }
    result = initial_context(graph(files), files, ["mod:target"])
    assert "Constants: USED = 2\nOmitted: 1 constants" in result.text
    assert result.omitted == ("constant LONG: too long",)
    assert "UNUSED" not in result.text and "Constants: IMPORTED" not in result.text


def test_constant_loads_respect_nested_scopes() -> None:
    files = {
        "mod.py": dedent("""\
            GLOBAL = 1
            VALUES = [GLOBAL for GLOBAL in ()]
            HIDDEN = 2
            SHADOW = 3
            NESTED = 4
            def target():
                def nested(SHADOW):
                    return SHADOW, NESTED
                values = [HIDDEN for HIDDEN in range(2)]
                return GLOBAL, nested, values
        """)
    }
    result = initial_context(graph(files), files, ["mod:target"])
    assert "Constants: GLOBAL = 1, NESTED = 4" in result.text
    assert "HIDDEN =" not in result.text and "SHADOW =" not in result.text


def test_constant_representations_and_module_rebindings() -> None:
    files = {
        "mod.py": dedent("""\
            DATA = ({'z', 'a'}, [{'x': {2, 1}}], set())
            VALUE: int
            VALUE = 3
            REBOUND = 2
            DEFAULT = 1
            def other(value=(DEFAULT := 2)): pass
            try: pass
            except Exception as REBOUND: pass
            def target(): return DATA, VALUE, REBOUND, DEFAULT
        """)
    }
    result = initial_context(graph(files), files, ["mod:target"])
    assert "Constants: DATA = ({'a', 'z'}, [{'x': {1, 2}}], set()), VALUE = 3" in result.text
    assert "REBOUND =" not in result.text and "DEFAULT =" not in result.text


def test_invalid_targets_and_unavailable_graph() -> None:
    files = {"mod.py": "class C: pass\ndef target(): pass"}
    snapshot = graph(files)
    invalid = ["missing", "mod", "mod:C", "missing"]
    result = initial_context(snapshot, files, invalid)
    assert result.text == "" and result.tokens == 0
    assert result.omitted == tuple(
        f"target {i}: not a function or method in the graph" for i in sorted(set(invalid))
    )
    mixed = initial_context(snapshot, files, [*invalid, "mod:target"])
    assert mixed.text.endswith("Omitted: 3 targets")
    errors = graph({"z.py": "def (", "a.py": "def ("})
    assert initial_context(errors, {}, []) == InitialContext(
        "", 0, ("graph has parse errors in a.py, z.py",)
    )


@pytest.mark.parametrize("source", [None, "def (", "# no definition at the graph span"])
def test_unavailable_source_skips_every_section(source: str | None) -> None:
    files = {"mod.py": "def target(): pass\ndef caller(): target()"}
    snapshot = graph(files)
    supplied = {} if source is None else {"mod.py": source}
    assert initial_context(snapshot, supplied, ["mod:target"]) == InitialContext(
        "", 0, ("target mod:target: source unavailable",)
    )


def test_long_signature_async_method_and_decorator_span() -> None:
    default = "é" * SIGNATURE_CHARS
    files = {
        "mod.py": f"def deco(fn): return fn\nclass C:\n"
        f"    @deco\n    async def target(self, value={default!r}) -> str:\n        return value"
    }
    result = initial_context(graph(files), files, ["mod:C.target"])
    signature = result.text.split("\n", 2)[2]
    assert signature.startswith("@deco\nasync def target(self, value=")
    assert len(signature) == SIGNATURE_CHARS and signature.endswith(" ...")
    assert result.omitted == ("signature of mod:C.target cut",)
    assert result.tokens == (len(result.text.encode()) + 2) // 3
    for budget in range(result.tokens + 1):
        assert (
            initial_context(graph(files), files, ["mod:C.target"], budget=budget).tokens <= budget
        )


def test_deterministic_shuffles_and_duplicate_edges(
    example: tuple[dict[str, str], GraphSnapshot, list[str]],
) -> None:
    files, snapshot, targets = example
    files = {**files, "other.py": "def unrelated(): pass"}
    snapshot = graph(files)
    original = snapshot.model_dump_json()
    expected = initial_context(snapshot, files, targets)
    possible = next(e for e in snapshot.edges if e.src == "mod:uncertain")
    exact = next(e for e in snapshot.edges if e.src == "mod:Runner.caller")
    extra = exact.model_copy(update={"confidence": "possible", "reason": possible.reason})
    for seed in range(10):
        rng = random.Random(seed)
        nodes, edges, ids = list(snapshot.nodes), [*snapshot.edges, extra], targets * 2
        rng.shuffle(nodes)
        rng.shuffle(edges)
        rng.shuffle(ids)
        shuffled = snapshot.model_copy(update={"nodes": nodes, "edges": edges})
        assert initial_context(shuffled, dict(reversed(list(files.items()))), ids) == expected
    assert snapshot.model_dump_json() == original


@pytest.mark.parametrize("callers", [True, False])
def test_list_limit_and_no_transitive_or_container_neighbors(callers: bool) -> None:
    names = [f"peer{i}" for i in range(LIMIT + 1)]
    if callers:
        source = "def target(): pass\n" + "\n".join(f"def {n}(): target()" for n in names)
        source += "\ndef indirect(): peer0()\ntarget()\nclass C:\n    target()"
    else:
        source = "def target():\n" + "".join(f"    {n}()\n" for n in names)
        source += "\n".join(f"def {n}(): pass" for n in names)
    files = {"mod.py": source}
    result = initial_context(graph(files), files, ["mod:target"])
    label = "Callers" if callers else "Callees"
    line = next(line for line in result.text.splitlines() if line.startswith(label))
    assert line.count("; exact)") == LIMIT
    assert f"peer{LIMIT} (" not in line and "indirect (" not in line
    assert result.omitted == (f"{'caller' if callers else 'callee'} mod:peer{LIMIT}: list limit",)
    assert result.text.endswith(f"Omitted: 1 {label.lower()}")


def test_only_oversight_dependency_is_neighbors() -> None:
    tree = ast.parse(Path(_initial_context.__file__).read_text())
    imports = []
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom) and item.module:
            imports.append(item.module)
        elif isinstance(item, ast.Import):
            imports.extend(a.name for a in item.names)
    assert [name for name in imports if name == "oversight" or name.startswith("oversight.")] == [
        "oversight.changes._neighbors"
    ]
