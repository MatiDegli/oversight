"""File reads, review saves and main file/graph transactions."""

from oversight.changes import _delivery as delivery
from oversight.changes import _proposal as proposal
from oversight.changes._state import SUSPECT, ChangeNotFound, ChangeRecord, WrongState
from oversight.changes._support import ChangeContext
from oversight.changes._versions import main_delta, rescan_delta, with_positions
from oversight.graph import DeltaCause
from oversight.workspace import (
    FileRead,
    InvalidPath,
    StaleFile,
    Tree,
    WorkspaceError,
    check_content,
)
from oversight_contracts.commands import FileContent, FileSaved
from oversight_contracts.common import AgentId


async def save_for_review(
    ctx: ChangeContext, agent_id: AgentId, path: str, content: str, base_hash: str
) -> str:
    """The person's save on the new side in review: write `path` in the agent's worktree,
    commit it as the user and re-propose (R6), so Approve never merges an untested edit; the
    file's new hash. Only from `ready`, checked again under the agent's lock, which the save
    holds throughout (`WrongState`); `StaleFile` when the file is not the one `base_hash` names.
    R9: with nothing to commit and HEAD still the last proposal's, nothing is sent. A save cut
    short after its write is re-proposed by the next Approve (R1)."""
    check_content(content)
    rec = ctx.state.agents.get(agent_id)
    if rec is None:
        raise ChangeNotFound(agent_id)
    check_editable(ctx, rec)
    async with ctx.workspace.locks(agent_id=agent_id):
        check_editable(ctx, rec)  # a decision may have taken it while the save waited
        saved = await rec.tree.write_text(path, content, expected_hash=base_hash)
        rec.touched.add(path)
        head, files = await proposal.commit(ctx, rec, "user")
        if head != rec.head_commit:
            await proposal.proposal(ctx, rec, head, files, "user", rec.summary)
    return saved


def check_editable(ctx: ChangeContext, rec: ChangeRecord) -> None:
    if rec.state != "ready":
        raise WrongState(f"{rec.change_id} is {rec.state}; a save in review needs ready")


async def read_file(ctx: ChangeContext, agent_id: AgentId | None, path: str) -> FileContent:
    """GET /files. Main is read under main's lock, which every mutation of main holds from its
    first git call to its last publish, so a read sees a whole commit, never the tree of a merge
    waiting for its tests, nor conflict markers before the abort; a suspect main is read as it
    stands. An agent's worktree is read under its own lock, in any state until its cleanup.
    `ChangeNotFound` for no live worktree, `FileNotFoundError` for a missing file, `InvalidPath`
    for a path the workspace refuses and for a file that is not UTF-8 text."""
    if agent_id is None:
        async with ctx.workspace.locks(main=True):
            read = await read_text(ctx, ctx.workspace.main, path)
    else:
        rec = ctx.state.agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        async with ctx.workspace.locks(agent_id=agent_id):
            if rec.cleaned:  # read under the lock a cleanup holds
                raise ChangeNotFound(agent_id)
            read = await read_text(ctx, rec.tree, path)
    return FileContent(path=path, content=read.content, hash=read.hash, agent_id=agent_id)


async def read_text(ctx: ChangeContext, tree: Tree, path: str) -> FileRead:
    """`tree.read_text`, with what a person's request can meet as `InvalidPath`: a path through
    a file, and a file that is not UTF-8 text (a directory is refused by the workspace)."""
    try:
        return await tree.read_text(path)
    except NotADirectoryError as error:
        raise InvalidPath(f"invalid path {path[:80]!r}: a parent is not a directory") from error
    except WorkspaceError as error:
        if isinstance(error.__cause__, UnicodeDecodeError):
            raise InvalidPath(f"{path[:80]!r} is not a UTF-8 text file") from error
        raise


async def save_main(ctx: ChangeContext, path: str, content: str, base_hash: str) -> FileSaved:
    """PUT /files on main: commit `content` for `path` as the user, a commit holding that file
    only, and publish one `graph.delta` (`user_edit`). One `_whole` step: a cancel of the
    request waits for the save to end, so none falls between the write, the commit and the
    publish. `StaleFile` when the file is missing or is not the one `base_hash` names (a save
    never creates a file), `InvalidPath`, `WrongState` for a suspect, dirty or mid-merge main.
    The same bytes commit nothing. A failed commit restores the file and raises
    `WorkspaceError`; the commit is the save, so a failed rescan after it still answers saved.
    Runs no tests: the next approval tests the combination (R5)."""
    return await ctx.whole(save_main_transaction(ctx, path, content, base_hash))


async def save_main_transaction(
    ctx: ChangeContext, path: str, content: str, base_hash: str
) -> FileSaved:
    # M4: a pending close-out usually sends its delta here, moving `snapshot`, before the
    # save's delta is built from it.
    check_content(content)
    # Refuse oversized existing files before pending events or git operations as well.
    try:
        await read_text(ctx, ctx.workspace.main, path)
    except FileNotFoundError:
        raise StaleFile(f"{path} no longer exists") from None
    await delivery.finish_pending(ctx)
    main = ctx.workspace.main
    async with ctx.workspace.locks(main=True):
        await delivery.drain_pending(ctx)
        blocked = await main_blocked(ctx)
        if blocked is not None:
            raise WrongState(f"{blocked}; nothing was saved")
        pre = (await ctx.git(main, "rev-parse", "HEAD")).strip()
        try:
            original = (await read_text(ctx, main, path)).content
        except FileNotFoundError:
            raise StaleFile(f"{path} no longer exists") from None
        saved = await main.write_text(path, content, expected_hash=base_hash)
        if saved == base_hash:
            return FileSaved(path=path, hash=saved)  # the same bytes: no commit, no event
        try:
            # `:(literal)`: the path is a file's name, never a glob or a pathspec's magic.
            await ctx.git(main, "add", "--", f":(literal){path}")
            await ctx.git(main, "commit", "-q", "-m", f"Edit {path}", commit_as="user")
        except Exception as failure:
            await settle_save(ctx, path, pre, original, saved, failure)
        try:
            head = (await ctx.git(main, "rev-parse", "HEAD")).strip()
            await publish_main(ctx, head, "user_edit")
        except Exception as error:
            # The snapshot stays: the next merge, save or rescan covers this commit.
            message = f"Main's graph was not updated after saving {path}: {error}"
            await ctx.report("graph", message, fatal=False)
        return FileSaved(path=path, hash=saved)


async def settle_save(
    ctx: ChangeContext, path: str, pre: str, original: str, saved: str, failure: Exception
) -> None:
    """B2: the save's `add` or `commit` failed, or timed out after it ran. Decide from git's
    facts first, then act. A commit that landed whole returns, and the save goes on. Main
    still at `pre`: put `original` back and stage it, so the index matches HEAD; confirmed
    clean, main stays trusted. Anything else, an unreadable fact or a restore that fails: main
    is suspect until restart. Either way raises `WorkspaceError` after its `error` event."""
    landed = await save_landed(ctx, path, pre, saved)
    if landed:
        return
    if landed is False and await restore_save(ctx, path, original, saved):
        await ctx.report(
            "git", f"Saving {path} failed ({failure}); main is unchanged.", fatal=False
        )
    else:
        ctx.state.suspect = True
        await ctx.report(
            "git",
            f"Saving {path} failed ({failure}) and main's state is unknown; "
            "reject the change and restart the server.",
            fatal=True,
        )
    raise WorkspaceError(f"saving {path} failed")


async def save_landed(ctx: ChangeContext, path: str, pre: str, saved: str) -> bool | None:
    """True when the commit landed whole: HEAD is a child of `pre` that changed `path` alone,
    main is clean and the file on disk is the saved one. False when main is at `pre` with no
    merge. None for anything else, and when a fact cannot be read."""
    main = ctx.workspace.main
    try:
        head = (await ctx.git(main, "rev-parse", "HEAD")).strip()
        if await ctx.yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
            return None
        if head == pre:
            return False
        parent = (await ctx.git(main, "rev-parse", "HEAD^")).strip()
        diff = ("diff", "--name-only", "--no-renames", "-z", pre, "HEAD")
        names = [name for name in (await ctx.git(main, *diff)).split("\0") if name]
        clean = not (await ctx.git(main, "status", "--porcelain")).strip()
        on_disk = (await main.read_text(path)).hash
    except Exception:
        return None
    return True if parent == pre and names == [path] and clean and on_disk == saved else None


async def restore_save(ctx: ChangeContext, path: str, original: str, saved: str) -> bool:
    """Write `original` back over the saved file and stage it; True once status is clean."""
    main = ctx.workspace.main
    try:
        await main.write_text(path, original, expected_hash=saved)
        await ctx.git(main, "add", "--", f":(literal){path}")
        return not (await ctx.git(main, "status", "--porcelain")).strip()
    except Exception:
        return False


async def rescan(ctx: ChangeContext) -> None:
    """POST /graph/rescan: read main again and publish one delta (`rescan`) if anything moved:
    a commit made behind the server's back, or one whose scan raised. One `_whole` step. Main
    that is suspect, mid-merge or dirty is not main's graph at any commit: refused with an
    `error` event and nothing published, until the contract lists `wrong_state` for it (P1).
    A scan or publish that raises sends an `error`, keeps `snapshot` and re-raises."""
    await ctx.whole(rescan_transaction(ctx))


async def rescan_transaction(ctx: ChangeContext) -> None:
    await delivery.finish_pending(ctx)
    async with ctx.workspace.locks(main=True):
        await delivery.drain_pending(ctx)
        blocked = await main_blocked(ctx)
        if blocked is not None:
            await ctx.report("graph", f"{blocked}; nothing was rescanned.", fatal=False)
            return
        try:
            head = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
            await publish_main(ctx, head, "rescan")
        except Exception as error:
            await ctx.report("graph", f"Main's graph was not rescanned: {error}", fatal=False)
            raise


async def main_blocked(ctx: ChangeContext) -> str | None:
    """Why main cannot take a commit or a rescan now, or None: M5's check, then `check_main`'s
    facts. The caller holds main's lock, and `check_main` takes it again."""
    if ctx.state.suspect:
        return SUSPECT
    clean = await check_main(ctx)
    if clean is None:
        return "Main's state could not be read"
    return None if clean else "Main has uncommitted edits or a merge in progress"


async def publish_main(ctx: ChangeContext, head: str, cause: DeltaCause) -> None:
    """M3: scan main at `head` and send the one delta from the graph the UI holds, then move
    `snapshot` to it with no await between. A rescan sends none when nothing moved."""
    new = await ctx.scan(head)
    old = ctx.state.snapshot
    delta = rescan_delta(old, new, head) if cause == "rescan" else main_delta(old, new, head, cause)
    if delta is None:
        return
    delivery.supersede_pending(ctx)
    await ctx.publish(delta)
    ctx.state.snapshot = with_positions(new, old)


async def check_main(ctx: ChangeContext) -> bool | None:
    """Under main's lock: True when main has no `MERGE_HEAD` and `status --porcelain` is empty,
    False otherwise, None when git fails or times out."""
    main = ctx.workspace.main
    async with ctx.workspace.locks(main=True):
        try:
            merging = await ctx.yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD")
            dirty = bool((await ctx.git(main, "status", "--porcelain")).strip())
        except (WorkspaceError, OSError):
            return None
    return not merging and not dirty
