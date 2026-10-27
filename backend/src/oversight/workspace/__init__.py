"""The runtime workspace, worktrees, the async process and git runner, locks, paths and tests.

Other modules import only what this file exports; everything else here is internal. The module
imports only `oversight_contracts` and the standard library.
"""

from oversight.workspace.core import Author, Tree, Workspace
from oversight.workspace.errors import (
    InvalidPath,
    LockOrderError,
    StaleFile,
    UnsafeDeletion,
    WorkspaceBusy,
    WorkspaceError,
)
from oversight.workspace.files import FileRead, content_hash
from oversight.workspace.junit import MAX_TESTS, MAX_XML_BYTES, RunProblem, parse_junit
from oversight.workspace.junit import Outcome as JUnitOutcome
from oversight.workspace.junit import TestRun as JUnitRun
from oversight.workspace.runner import ProcessResult, run_process
from oversight.workspace.suite import (
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    validate_suite_execution,
)

__all__ = [
    "MAX_TESTS",
    "MAX_XML_BYTES",
    "Author",
    "FileRead",
    "InvalidPath",
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
    "content_hash",
    "parse_junit",
    "run_process",
    "validate_suite_execution",
]
