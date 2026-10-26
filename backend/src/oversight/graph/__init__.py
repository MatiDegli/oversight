"""Parsing, name resolution, deltas, the span index, change graphs and the impact cone.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.graph._workspace import WorkspaceGraph

__all__ = ["WorkspaceGraph"]
