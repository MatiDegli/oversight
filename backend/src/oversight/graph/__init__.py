"""Parsing, name resolution, deltas, the span index, change graphs, the impact cone and the layout.

Other modules import only what this file exports; everything else here is internal. Every function
is synchronous and runs no process: callers run the slow ones in a thread.
"""

from oversight.graph._change import change_graph
from oversight.graph._delta import DeltaCause, graph_delta
from oversight.graph._impact import (
    NodeNotFoundError,
    change_cone,
    impact_cone,
    node_cone,
    possible_callers,
)
from oversight.graph._layout import read_layout
from oversight.graph._test_reach import TestReach, call_graph_test_reach
from oversight.graph._version import CodeVersion, parse_version
from oversight.graph._workspace import WorkspaceGraph

__all__ = [
    "CodeVersion",
    "DeltaCause",
    "NodeNotFoundError",
    "TestReach",
    "WorkspaceGraph",
    "call_graph_test_reach",
    "change_cone",
    "change_graph",
    "graph_delta",
    "impact_cone",
    "node_cone",
    "parse_version",
    "possible_callers",
    "read_layout",
]
