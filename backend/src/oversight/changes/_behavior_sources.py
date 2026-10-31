"""One source inventory and class index per side of finite input discovery."""

import ast
import inspect
from functools import cached_property

from oversight.changes._behavior_inputs import module_name
from oversight.changes._shortcuts_common import Source, check_deadline, is_test_file


class Sources:
    def __init__(self, files: dict[str, str], deadline: float = float("inf")) -> None:
        self.files = files
        self.deadline = deadline
        self.constructors: dict[tuple[str, str], tuple[inspect.Signature, tuple[str, ...]]] = {}
        self.parsed: dict[str, Source | None] = {}
        self._parents: dict[int, dict[ast.AST, ast.AST]] = {}

    def get(self, path: str) -> Source:
        check_deadline(self.deadline)
        if path not in self.parsed:
            self.parsed[path] = None
            self.parsed[path] = Source(self.files[path], self.deadline)
        source = self.parsed[path]
        if source is None:
            raise ValueError("invalid source")
        return source

    @cached_property
    def modules(self) -> dict[str, list[str]]:
        modules: dict[str, list[str]] = {}
        for path in self.files:
            check_deadline(self.deadline)
            if path.endswith(".py"):
                modules.setdefault(module_name(path), []).append(path)
        return modules

    @cached_property
    def classes(self) -> dict[str, list[tuple[str, ast.ClassDef]]]:
        classes: dict[str, list[tuple[str, ast.ClassDef]]] = {}
        for module, paths in self.modules.items():
            for path in paths:
                check_deadline(self.deadline)
                if is_test_file(path):
                    continue
                try:
                    source = self.get(path)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                classes.setdefault(module, []).extend(
                    (path, node) for node in source.tree.body if isinstance(node, ast.ClassDef)
                )
        return classes

    def parents(self, source: Source) -> dict[ast.AST, ast.AST]:
        key = id(source)
        if key not in self._parents:
            parents: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(source.tree):
                check_deadline(self.deadline)
                parents.update((child, node) for child in ast.iter_child_nodes(node))
            self._parents[key] = parents
        return self._parents[key]
