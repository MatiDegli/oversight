"""The workspace's errors. The facade exports them; callers map them to HTTP codes."""


class WorkspaceError(Exception):
    """Base class: a workspace operation failed and changed nothing it should not have."""


class FileTooLarge(WorkspaceError):
    """A file or replacement content exceeds the editable-file byte ceiling (HTTP 413)."""


class InvalidPath(WorkspaceError):
    """A path escapes the tree or breaks a path rule (HTTP 422, `invalid_path`)."""


class StaleFile(WorkspaceError):
    """The file's content is not the one the caller based the write on (HTTP 409, `stale_file`)."""


class UnsafeDeletion(WorkspaceError):
    """A deletion guard refused: a wrong target, an unregistered worktree, an unmerged branch."""


class WorkspaceBusy(WorkspaceError):
    """A startup-only operation was called while the workspace is in use, or twice."""


class LockOrderError(WorkspaceError):
    """A lock was taken out of order, or git ran without the lock of its tree."""
