"""The preset layout: where each node sits on the graph, read from a JSON file."""

import logging
import math
from collections.abc import Mapping
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from oversight.limits import SizeLimitError, bounded_text
from oversight_contracts.common import NodeId
from oversight_contracts.graph import Point

_log = logging.getLogger(__name__)
_LAYOUT = TypeAdapter(dict[NodeId, Point])


class _Unusable(Exception):
    """The layout file exists but cannot be used; the message says why."""


def read_layout(path: Path) -> dict[NodeId, Point]:
    """Positions by node id, from a JSON object `{node_id: {"x": 1, "y": 2}}`. A missing file, an
    unreadable one and a malformed one all mean no positions: one log line names the file and the
    reason, and the graph places its own nodes. A preset layout is a nicety, so a bad file never
    stops the server. Ids are not checked against a graph: `assemble` ignores the ones it does not
    contain."""
    try:
        return _parse(path)
    except FileNotFoundError:
        _log.warning("no layout file at %s: the graph will place its own nodes", path)
    except _Unusable as exc:
        _log.warning(
            "layout file %s is unusable, %s: the graph will place its own nodes", path, exc
        )
    return {}


def _parse(path: Path) -> dict[NodeId, Point]:
    try:
        text = bounded_text(path)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, SizeLimitError) as exc:
        raise _Unusable(f"it cannot be read: {exc}") from exc
    try:
        # Strict: a coordinate that is a string is a mistake, not a number to coerce.
        layout = _LAYOUT.validate_json(text, strict=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the file"
        raise _Unusable(f"it is malformed at {where}: {first['msg']}") from exc
    for node_id, point in layout.items():
        if not (math.isfinite(point.x) and math.isfinite(point.y)):
            raise _Unusable(f"the position of {node_id} is not a finite number")
    return layout


def positions_of(layout: Mapping[NodeId, Point], node_ids: list[NodeId]) -> dict[NodeId, Point]:
    """The layout's entries for exactly these nodes: ids the layout does not know are left out, and
    so are layout ids that are not among the nodes."""
    return {nid: layout[nid] for nid in node_ids if nid in layout}
