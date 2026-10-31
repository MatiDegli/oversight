"""Pure protected-path matching; no list loading, filesystem expansion or authority."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from re import match

from oversight_contracts.common import FileStatus
from oversight_contracts.events import ChangeProposed, FileStat
from oversight_contracts.policy import EvidenceRef

BUILTIN_TABLE_VERSION: int = 1
# docs/additions.md:270 supplies the categories; the coordinator approved these patterns.
# Basename patterns apply at any depth only for manifests/lockfiles and test configuration.
BUILTIN_CATEGORIES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    (
        "manifest_lockfile",
        (
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements*.txt",
            "uv.lock",
            "poetry.lock",
            "Pipfile",
            "Pipfile.lock",
        ),
        True,
    ),
    ("ci", (".github/**", ".gitlab-ci.yml"), False),
    (
        "test_configuration",
        ("pytest.ini", "tox.ini", "noxfile.py", ".coveragerc", "conftest.py"),
        True,
    ),
    (
        "security",
        (".env", ".env.*", ".gitignore", ".pre-commit-config.yaml", ".gitleaks.toml"),
        False,
    ),
)
TOUCHING_STATUSES: dict[FileStatus, bool] = {"added": True, "modified": True, "deleted": True}


@dataclass(frozen=True, kw_only=True)
class ProtectedPathSnapshot:
    """Captured list and its receipt. paths=None selects the built-in category table.

    An explicit tuple uses exact membership, including literal glob characters. An unavailable
    or unreadable source is represented by PolicyInputs.protected_paths=None, never paths=().
    Built-in use also requires a receipt identifying the table version, supplied by the caller.
    """

    paths: tuple[str, ...] | None = None
    complete: bool = True
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ChangedFileInventory:
    """A complete captured diff, retaining statuses and the source receipt even when empty."""

    files: tuple[FileStat, ...]
    complete: bool
    evidence: tuple[EvidenceRef, ...] = ()


def capture_proposal_paths(
    proposal: ChangeProposed, *, proposal_seq: int | None, run_started_seq: int | None
) -> tuple[ProtectedPathSnapshot, ChangedFileInventory] | None:
    """Capture confirmed sources; the caller supplies retained receipts, never inferred seqs.

    The run receipt identifies the immutable built-in table version recorded in DECISIONS.md.
    The proposal receipt witnesses its complete inventory, including an explicitly empty diff.
    """
    if proposal_seq is None or run_started_seq is None or run_started_seq >= proposal_seq:
        return None
    return (
        ProtectedPathSnapshot(evidence=(EvidenceRef(event_seq=run_started_seq),)),
        ChangedFileInventory(
            files=tuple(proposal.files),
            complete=True,
            evidence=(EvidenceRef(event_seq=proposal_seq),),
        ),
    )


@dataclass(frozen=True, kw_only=True)
class ProtectedPathMatch:
    """Reproducible private diagnostic; the assessment wire format remains CheckInputs."""

    path: str
    category: str
    pattern: str


@dataclass(frozen=True, kw_only=True)
class ProtectedPathCheck:
    matches: tuple[ProtectedPathMatch, ...]
    evidence: tuple[EvidenceRef, ...]


def _valid_path(path: object) -> bool:
    # Lexical validation only, including deleted files: never resolve or open a path.
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and match(r"^[A-Za-z]:", path) is None
        and "\\" not in path
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and not any(part in {"", ".", "..", ".git"} for part in path.split("/"))
    )


def _known_status(status: object) -> bool:
    return isinstance(status, str) and status in TOUCHING_STATUSES


def _matches(path: str, snapshot: ProtectedPathSnapshot) -> tuple[ProtectedPathMatch, ...]:
    if snapshot.paths is not None:
        return (
            (ProtectedPathMatch(path=path, category="explicit", pattern=path),)
            if path in snapshot.paths
            else ()
        )
    return tuple(
        ProtectedPathMatch(path=path, category=category, pattern=pattern)
        for category, patterns, basename in BUILTIN_CATEGORIES
        for pattern in patterns
        if fnmatchcase(path.rsplit("/", 1)[-1] if basename else path, pattern)
    )


def check_protected_paths(
    snapshot: ProtectedPathSnapshot | None, inventory: ChangedFileInventory | None
) -> ProtectedPathCheck | None:
    """Validate every input before counting. None is an unknown wait, even beside a hit.

    Callers capture the table/list version and the complete changed-file source for this target;
    references are witnesses, not permission to infer a missing list or status. No I/O occurs.
    """
    if (
        snapshot is None
        or inventory is None
        or snapshot.complete is not True
        or inventory.complete is not True
        or not snapshot.evidence
        or not inventory.evidence
        or (snapshot.paths is not None and not all(_valid_path(p) for p in snapshot.paths))
        or any(not _valid_path(f.path) or not _known_status(f.status) for f in inventory.files)
    ):
        return None
    matches = tuple(
        hit
        for path in sorted({f.path for f in inventory.files})
        for hit in _matches(path, snapshot)
    )
    sources = (*snapshot.evidence, *inventory.evidence)
    evidence = tuple(
        dict.fromkeys(
            (
                *sources,
                *(
                    EvidenceRef(event_seq=ref.event_seq, git_object=ref.git_object, path=path)
                    for path in sorted({hit.path for hit in matches})
                    for ref in sources
                ),
            )
        )
    )
    return ProtectedPathCheck(matches=matches, evidence=evidence)
