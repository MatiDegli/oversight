"""Explicit pinned-source fetch and offline bare graph export; neither is a check prerequisite."""

import argparse
import asyncio
import shutil
import uuid
from pathlib import Path

from oversight.graph import WorkspaceGraph
from oversight.settings import REPO_ROOT
from oversight.workspace import (
    BOLTONS,
    SOURCE_GIT_ENV,
    SourceProfile,
    Spawn,
    run_process,
    validate_source,
)


async def fetch_source(
    profile: SourceProfile, runtime_root: Path, *, spawn: Spawn = run_process
) -> Path:
    """Clone only an absent checkout; an existing wrong pin is always left untouched."""
    root = profile.checkout(runtime_root)
    if root.resolve() != root:
        raise ValueError(f"{profile.name}: refusing a source path through a symbolic link")
    if root.exists():
        return await validate_source(profile, runtime_root, spawn=spawn)
    root.parent.mkdir(parents=True, exist_ok=True)
    # Claim an empty directory atomically; Git accepts it, and cleanup owns only this path.
    root.mkdir()
    try:
        for argv, cwd in (
            (
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    "--no-hardlinks",
                    "--",
                    profile.upstream,
                    str(root),
                ],
                root.parent,
            ),
            (["git", "checkout", "--detach", profile.commit], root),
        ):
            result = await spawn(argv, cwd=cwd, timeout_s=120, env=SOURCE_GIT_ENV)
            if result.returncode != 0 or result.timed_out:
                raise ValueError(f"{profile.name}: {argv[1]} failed: {result.stderr}")
        return await validate_source(profile, runtime_root, spawn=spawn)
    except BaseException:
        await asyncio.to_thread(shutil.rmtree, root)
        raise


async def export_graph(
    profile: SourceProfile, runtime_root: Path, *, spawn: Spawn = run_process
) -> Path:
    """Parse the whole pinned tree, with no server, models, network or layout generation."""
    root = await validate_source(profile, runtime_root, spawn=spawn)
    snapshot = await asyncio.to_thread(WorkspaceGraph(root).scan, profile.commit)
    output = runtime_root / "graphs" / f"{profile.name}.snapshot.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(snapshot.model_dump_json(indent=2) + "\n")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{profile.name} {profile.commit}: {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("fetch-boltons", "graph-boltons"))
    args = parser.parse_args()
    runtime = REPO_ROOT / ".runtime"
    try:
        if args.operation == "fetch-boltons":
            path = asyncio.run(fetch_source(BOLTONS, runtime))
            print(f"{BOLTONS.name} {BOLTONS.commit}: {path}")
        else:
            asyncio.run(export_graph(BOLTONS, runtime))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
