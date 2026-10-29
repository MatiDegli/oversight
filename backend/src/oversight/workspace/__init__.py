"""The runtime workspace, worktrees, the async process and git runner, locks, paths and tests.

Other modules import only what this file exports; everything else here is internal. The module
imports only `oversight_contracts`, shared byte limits and the standard library.
"""

from oversight.workspace.behavior_run import (
    PER_CALL_TIMEOUT_S,
    BehaviorObservation,
    BehaviorTarget,
    TypedResult,
)
from oversight.workspace.capability import OPEN_NOTICE, IsolationCapability, IsolationPolicy
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
from oversight.workspace.integrity import (
    IntegrityError,
    MergeGuard,
    prepare_merge_guard,
    verify_merge_abort,
    verify_merge_guard,
)
from oversight.workspace.isolation import IsolatedRunner, IsolationUnavailable
from oversight.workspace.isolation_probe import print_isolation_probe
from oversight.workspace.junit import MAX_TESTS, MAX_XML_BYTES, RunProblem, parse_junit
from oversight.workspace.junit import Outcome as JUnitOutcome
from oversight.workspace.junit import TestRun as JUnitRun
from oversight.workspace.measured_report import CallReport, CallTarget, Invocation
from oversight.workspace.probes import base_probe_checkout
from oversight.workspace.runner import ProcessResult, Spawn, run_process
from oversight.workspace.source import (
    BOLTONS,
    SOURCE_GIT_ENV,
    SourceProfile,
    select_source,
    validate_source,
)
from oversight.workspace.suite import (
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    validate_suite_execution,
)
from oversight.workspace.suite_guard import configure_suite
from oversight.workspace.tree import TREE_TIMEOUT_S, list_tree

__all__ = [
    "BOLTONS",
    "MAX_TESTS",
    "MAX_XML_BYTES",
    "OPEN_NOTICE",
    "PER_CALL_TIMEOUT_S",
    "SOURCE_GIT_ENV",
    "TREE_TIMEOUT_S",
    "Author",
    "BehaviorObservation",
    "BehaviorTarget",
    "CallReport",
    "CallTarget",
    "FileRead",
    "FileTooLarge",
    "IntegrityError",
    "InvalidPath",
    "Invocation",
    "IsolatedRunner",
    "IsolationCapability",
    "IsolationPolicy",
    "IsolationUnavailable",
    "JUnitOutcome",
    "JUnitRun",
    "LockOrderError",
    "MergeGuard",
    "ProcessResult",
    "RunProblem",
    "SourceProfile",
    "Spawn",
    "StaleFile",
    "SuiteInterrupted",
    "SuiteRun",
    "SuiteUnavailable",
    "Tree",
    "TypedResult",
    "UnsafeDeletion",
    "Workspace",
    "WorkspaceBusy",
    "WorkspaceError",
    "base_probe_checkout",
    "check_content",
    "configure_suite",
    "content_hash",
    "list_tree",
    "parse_junit",
    "prepare_merge_guard",
    "print_isolation_probe",
    "run_process",
    "select_source",
    "valid_agent_id",
    "validate_source",
    "validate_suite_execution",
    "verify_merge_abort",
    "verify_merge_guard",
]
