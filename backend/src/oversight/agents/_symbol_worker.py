"""Child-only Jedi analysis: no original worktree reads, imports or automatic path additions."""

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast

import jedi  # pyright: ignore[reportMissingTypeStubs]

from oversight.workspace import is_secret_path


class _Name(Protocol):
    name: str
    module_path: Path | None
    line: int | None
    column: int
    type: str
    module_name: str

    def parent(self) -> "_Name": ...
    def is_definition(self) -> bool: ...


class _Script(Protocol):
    def search(self, string: str, *, all_scopes: bool) -> Iterable[_Name]: ...
    def goto(self, line: int | None, column: int, *, follow_imports: bool) -> list[_Name]: ...
    def get_references(self, line: int | None, column: int) -> list[_Name]: ...
    def get_names(
        self, *, all_scopes: bool, definitions: bool, references: bool
    ) -> list[_Name]: ...


def _guard(original: Path, snapshot: Path) -> None:
    """Jedi may inspect trusted libraries, but cannot follow source-supplied paths to secrets.

    The snapshot contains only Tree-validated Python text. Block imports from it as well as
    all filesystem inspection of the original tree, including attempts through sys.path edits.
    """
    trusted = [Path(path).resolve() for path in sys.path if path]
    trusted.append(snapshot.parent.resolve())

    def audit(event: str, arguments: tuple[object, ...]) -> None:
        if event not in {"open", "os.listdir", "os.scandir", "import"}:
            return
        value = arguments[1] if event == "import" else arguments[0]
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(cast(str | bytes | os.PathLike[str], value))).resolve()
        if (
            path.is_relative_to(original)
            or is_secret_path(path.as_posix())
            or not any(path.is_relative_to(base) for base in trusted)
            or (event == "import" and path.is_relative_to(snapshot))
        ):
            raise PermissionError("symbol analysis refused an untrusted path")

    sys.addaudithook(audit)


def _inside(snapshot: Path, definition: _Name) -> tuple[str, int] | None:
    if definition.module_path is None or definition.line is None:
        return None
    path = Path(definition.module_path).resolve()
    if not path.is_relative_to(snapshot):
        return None
    relative = path.relative_to(snapshot).as_posix()
    if is_secret_path(relative):
        return None
    return relative, definition.line


def _references(script: _Script, definition: _Name, path: Path) -> list[_Name]:
    if definition.module_path == path:
        return script.get_references(definition.line, definition.column)
    # A dotted search can already resolve to a library definition. Its cursor belongs to
    # that library, not to this script: match local reference targets instead.
    found: list[_Name] = []
    for reference in script.get_names(all_scopes=True, definitions=False, references=True):
        if reference.name != definition.name:
            continue
        targets = script.goto(reference.line, reference.column, follow_imports=True)
        if any(
            (target.module_path, target.line, target.column)
            == (definition.module_path, definition.line, definition.column)
            for target in targets
        ):
            found.append(reference)
    return found


def analyze(snapshot: Path, name: str, under: str, cap: int) -> dict[str, object]:
    api = cast(Any, jedi)
    api.settings.cache_directory = str(snapshot.parent / "cache")
    project = api.Project(
        snapshot, load_unsafe_extensions=False, added_sys_path=(), smart_sys_path=False
    )
    # Keep static import resolution in this process, under the filesystem audit guard.
    environment = api.InterpreterEnvironment()
    definitions: set[tuple[str, int, str]] = set()
    references: set[tuple[str, int, str]] = set()
    modules: set[str] = set()

    def in_scope(path: str) -> bool:
        return not under or path == under or path.startswith(under.rstrip("/") + "/")

    paths = (p for p in snapshot.rglob("*") if p.is_file() and p.suffix in {".py", ".pyi"})
    for path in sorted(paths):
        relative = path.relative_to(snapshot).as_posix()
        if not in_scope(relative):
            continue
        script = cast(_Script, api.Script(path=path, project=project, environment=environment))
        module = relative.removesuffix(path.suffix).replace("/", ".").removesuffix(".__init__")
        # Resolve an explicit repository module prefix without adding it to Jedi's sys.path.
        query = name.removeprefix(module + ".")
        found = list(script.search(name, all_scopes=True))
        if not found and query != name:
            found = list(script.search(query, all_scopes=True))
        for definition in found:
            # Following an import may reach a library; its source never enters our response.
            targets = (
                script.goto(definition.line, definition.column, follow_imports=True)
                if definition.module_path == path
                else [definition]
            )
            for target in targets or [definition]:
                location = _inside(snapshot, target)
                if location is None:
                    modules.add(target.module_name)
                elif in_scope(location[0]):
                    kind = target.type
                    if kind == "function" and target.parent().type == "class":
                        kind = "method"
                    definitions.add((*location, kind))
            for reference in _references(script, definition, path):
                location = _inside(snapshot, reference)
                if location is not None and in_scope(location[0]) and not reference.is_definition():
                    references.add((*location, "reference"))
    matches = sorted(definitions) + sorted(references)
    return {
        "matches": matches[:cap],
        "modules": sorted(modules)[:cap],
        "definitions": len(definitions) + len(modules),
        "references": len(references),
    }


def main() -> None:
    original, snapshot = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    _guard(original, snapshot)
    result = analyze(snapshot, sys.argv[3], sys.argv[4], int(sys.argv[5]))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
