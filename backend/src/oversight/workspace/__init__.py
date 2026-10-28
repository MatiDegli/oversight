"""The runtime workspace, worktrees, the async process and git runner, locks, paths and tests.

Other modules import only what this file exports; everything else here is internal. The module
imports only `oversight_contracts`, shared byte limits and the standard library.
"""

from oversight.workspace.core import Author, Tree, Workspace
from oversight.workspace.errors import (
    FileTooLarge,
    InvalidPath,
    LockOrderError,
    StaleFile,
    UnsafeDeletion,
    WorkspaceBusy,
    WorkspaceError,
)
from oversight.workspace.files import FileRead, check_content, content_hash, valid_agent_id
from oversight.workspace.isolation import IsolatedRunner, IsolationUnavailable
from oversight.workspace.isolation_probe import print_isolation_probe
from oversight.workspace.junit import MAX_TESTS, MAX_XML_BYTES, RunProblem, parse_junit
from oversight.workspace.junit import Outcome as JUnitOutcome
from oversight.workspace.junit import TestRun as JUnitRun
from oversight.workspace.probes import base_probe_checkout
from oversight.workspace.runner import ProcessResult, run_process
from oversight.workspace.suite import (
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    validate_suite_execution,
)
from oversight.workspace.tree import TREE_TIMEOUT_S, list_tree

__all__ = [
    "MAX_TESTS",
    "MAX_XML_BYTES",
    "TREE_TIMEOUT_S",
    "Author",
    "FileRead",
    "FileTooLarge",
    "InvalidPath",
    "IsolatedRunner",
    "IsolationUnavailable",
    "JUnitOutcome",
    "JUnitRun",
    "LockOrderError",
    "ProcessResult",
    "RunProblem",
    "StaleFile",
    "SuiteInterrupted",
    "SuiteRun",
    "SuiteUnavailable",
    "Tree",
    "UnsafeDeletion",
    "Workspace",
    "WorkspaceBusy",
    "WorkspaceError",
    "base_probe_checkout",
    "check_content",
    "content_hash",
    "list_tree",
    "parse_junit",
    "print_isolation_probe",
    "run_process",
    "valid_agent_id",
    "validate_suite_execution",
]
