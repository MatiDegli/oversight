"""The runtime workspace, worktrees, the async process and git runner, locks, paths and tests.

Other modules import only what this file exports; everything else here is internal. The module
imports only `oversight_contracts`, shared byte limits and the standard library.
"""

from oversight.workspace._behavior_values import MAX_RANGE_ITEMS
from oversight.workspace._platform import (
    assign_job,
    close_job,
    create_kill_job,
    is_link_like,
    job_pids,
    preflight_placement,
    replace_retry,
    resume_suspended,
    terminate_job,
    unlink_retry,
)
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
from oversight.workspace.example_criteria import RenderedCriteria, parse_cases, render_examples
from oversight.workspace.files import (
    FileRead,
    check_content,
    content_hash,
    split_relative,
    valid_agent_id,
)
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
from oversight.workspace.revert import (
    InverseCandidate,
    InverseClassification,
    InverseError,
    InverseOutcome,
    apply_inverse,
    classify_inverse,
    inverse_suite_problem,
    prepare_inverse,
)
from oversight.workspace.runner import ProcessResult, Spawn, run_process
from oversight.workspace.secret_paths import SECRET_FILE_REFUSAL, is_secret_path, secret_refusal
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
    "MAX_RANGE_ITEMS",
    "MAX_TESTS",
    "MAX_XML_BYTES",
    "OPEN_NOTICE",
    "PER_CALL_TIMEOUT_S",
    "SECRET_FILE_REFUSAL",
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
    "InverseCandidate",
    "InverseClassification",
    "InverseError",
    "InverseOutcome",
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
    "RenderedCriteria",
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
    "apply_inverse",
    "assign_job",
    "base_probe_checkout",
    "check_content",
    "classify_inverse",
    "close_job",
    "configure_suite",
    "content_hash",
    "create_kill_job",
    "inverse_suite_problem",
    "is_link_like",
    "is_secret_path",
    "job_pids",
    "list_tree",
    "parse_cases",
    "parse_junit",
    "preflight_placement",
    "prepare_inverse",
    "prepare_merge_guard",
    "print_isolation_probe",
    "render_examples",
    "replace_retry",
    "resume_suspended",
    "run_process",
    "secret_refusal",
    "select_source",
    "split_relative",
    "terminate_job",
    "unlink_retry",
    "valid_agent_id",
    "validate_source",
    "validate_suite_execution",
    "verify_merge_abort",
    "verify_merge_guard",
]
