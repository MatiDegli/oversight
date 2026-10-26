"""From a file and a line to the innermost node whose range holds it."""

from collections.abc import Iterable

from oversight_contracts.graph import GraphNode


class SpanIndex:
    def __init__(self, nodes: Iterable[GraphNode]) -> None:
        nodes = list(nodes)
        parents = {n.id: n.parent_id for n in nodes}

        def depth(node_id: str) -> int:
            count = 0
            parent = parents.get(node_id)
            while parent is not None:
                count += 1
                parent = parents.get(parent)
            return count

        # Ranges nest, so painting outer nodes first leaves each line with its innermost node.
        ordered = sorted(nodes, key=lambda n: (n.start_line, -n.end_line, depth(n.id)))
        self._lines: dict[str, list[str | None]] = {}
        for node in ordered:
            lines = self._lines.setdefault(node.path, [])
            if len(lines) < node.end_line:
                lines.extend([None] * (node.end_line - len(lines)))
            lines[node.start_line - 1 : node.end_line] = [node.id] * (
                node.end_line - node.start_line + 1
            )

    def node_at(self, path: str, line: int) -> str | None:
        """The innermost node at a 1-based line; None outside every parsed file's lines."""
        lines = self._lines.get(path)
        if lines is None or not 1 <= line <= len(lines):
            return None
        return lines[line - 1]
