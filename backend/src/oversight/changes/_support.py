import logging
from dataclasses import dataclass

from oversight.agents import Publish
from oversight.changes._state import ChangeState
from oversight.events import Clock
from oversight.graph import (
    WorkspaceGraph,
)
from oversight.workspace import (
    Workspace,
)

_log = logging.getLogger("oversight.changes._manager")


@dataclass
class ChangeContext:
    """Dependencies and utilities shared by stages; state has exactly one owner."""

    workspace: Workspace
    publish: Publish
    main_graph: WorkspaceGraph
    clock: Clock
    timeout: float
    state: ChangeState
