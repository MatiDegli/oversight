"""Trusted reviewed-preview translation. The reviewed self app remains refused by admission."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from oversight.workspace.locks import offload
from oversight.workspace.preview_boundary import ReadOnlyMount
from oversight.workspace.preview_boundary_entry import (
    SYSTEM_DIRECTORIES,
    SYSTEM_FILES,
    TOOL_TARGETS,
    manifest_digest,
)
from oversight.workspace.preview_preparation import (
    PreparationInputs,
    PreparedPreview,
    prepare_preview,
    verify_prepared_manifests,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def canonical_exclusions(exclusions: tuple[Path, ...]) -> tuple[Path, ...]:
    # Canonical operands also cover hosts with aliases for HOME, /mnt or /run.
    return tuple(
        dict.fromkeys(
            p.resolve() for p in (*exclusions, REPO_ROOT, Path.home(), Path("/mnt"), Path("/run"))
        )
    )


def validate_storage(inputs: PreparationInputs) -> None:
    exclusions = canonical_exclusions(inputs.excluded_roots)
    writable = (inputs.tree, inputs.preview_directory, inputs.preparation_storage)
    if any(overlaps(path.resolve(), excluded) for path in writable for excluded in exclusions):
        raise ValueError("reviewed writable storage overlaps an excluded root")


async def prepare_reviewed(inputs: PreparationInputs) -> PreparedPreview:
    validate_storage(inputs)
    # The leaf's async API currently does synchronous inventory/copy work. Run its entire
    # coroutine on a worker loop, and settle that worker before releasing any ownership.
    return await offload(lambda: asyncio.run(prepare_preview(inputs)))


@dataclass(frozen=True)
class ReviewedRoots:
    tree: Path
    state: Path
    scratch: Path
    readonly: tuple[ReadOnlyMount, ...]
    exclusions: tuple[Path, ...]


def boundary_roots(prepared: PreparedPreview, exclusions: tuple[Path, ...]) -> ReviewedRoots:
    excluded = canonical_exclusions(exclusions)
    scratch = prepared.root
    writable = (prepared.tree, prepared.state, scratch)
    if any(overlaps(p.resolve(strict=True), e) for p in writable for e in excluded):
        raise ValueError("reviewed writable root overlaps an excluded root")
    verify_prepared_manifests(prepared)
    roles = {target: role for role, target in TOOL_TARGETS.items()}
    mounts: list[ReadOnlyMount] = []
    for mapping in prepared.root_mappings:
        if not mapping.read_only:
            if (mapping.source, mapping.target) not in (
                (prepared.tree, Path("/T")),
                (prepared.state, Path("/state")),
            ):
                raise ValueError("unexpected writable root mapping")
            continue
        source = mapping.source.resolve(strict=True)
        if any(overlaps(source, e) for e in (*excluded, *writable)):
            raise ValueError("reviewed mount overlaps an excluded root")
        if not any(i.root == source for i in prepared.manifests):
            raise ValueError("unrecorded boundary mount")
        target = mapping.target.as_posix()
        role = roles.get(target)
        if role is None and mapping.target.is_relative_to("/opt/deps"):
            role = "dependencies"
        if role is not None:
            mounts.append(ReadOnlyMount(source, target, role, manifest_digest(source)))
    # Preparation can inventory a directory of selected system entries. The boundary
    # mounts only its fixed individual system files/directories, using the real system
    # identity and comparing it with the recorded prepared bytes first.
    for target in (*SYSTEM_FILES, *SYSTEM_DIRECTORIES):
        matches = [
            m
            for m in prepared.root_mappings
            if m.read_only and Path(target).is_relative_to(m.target)
        ]
        if len(matches) != 1:
            raise ValueError("missing recorded system mount")
        mapping = matches[0]
        selected = mapping.source / Path(target).relative_to(mapping.target)
        source = Path(target).resolve(strict=True)
        digest = manifest_digest(source)
        if manifest_digest(selected.resolve(strict=True)) != digest:
            raise ValueError("recorded system mount changed")
        mounts.append(ReadOnlyMount(source, target, "system", digest))
    if {(m.source, m.target) for m in prepared.root_mappings if not m.read_only} != {
        (prepared.tree, Path("/T")),
        (prepared.state, Path("/state")),
    }:
        raise ValueError("missing writable root mapping")
    return ReviewedRoots(prepared.tree, prepared.state, scratch, tuple(mounts), excluded)
