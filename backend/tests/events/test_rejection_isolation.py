"""One bad producer cannot poison a writer batch or leave publishers awaiting forever."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from oversight.events import (
    EventRejected,
    EventStore,
    LogError,
    StoredEvent,
    read_jsonl,
    read_store,
)
from oversight.events.player import play
from oversight_contracts.events import ChangeResolved, ErrorReport
from oversight_contracts.review import CrossReview, ReviewFinding

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.anyio
RUN = "run-20261027-140312"


def report(message: str = "test") -> ErrorReport:
    return ErrorReport(source="server", message=message, agent_id=None, change_id=None, fatal=False)


def resolution(**updates: object) -> ChangeResolved:
    values = dict(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        decision="rejected",
        reason=None,
        comment=None,
        merge=None,
        merge_commit=None,
        conflict=None,
    )
    return ChangeResolved.model_validate({**values, **updates})


def review() -> CrossReview:
    return CrossReview(
        change_id="change-1",
        revision=1,
        base_commit="base",
        head_commit="head",
        main_commit="main",
        candidate_tree="tree",
        author_model="author",
        reviewer_model="reviewer",
        reviewer_provider="fake",
        reasoning=None,
        verdict="clean",
        findings=[],
        complete=True,
        problems=[],
    )


async def test_valid_invalid_valid_batch_has_contiguous_commits_and_writer_survives(
    tmp_path: Path,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        outcomes = await asyncio.gather(
            store.publish(report("first")),
            store.publish(resolution()),
            store.publish(report("last")),
            return_exceptions=True,
        )
        assert isinstance(outcomes[0], StoredEvent) and outcomes[0].seq == 1
        assert isinstance(outcomes[1], EventRejected)
        assert isinstance(outcomes[2], StoredEvent) and outcomes[2].seq == 2
        assert (await store.publish(report())).seq == 3
    assert store.last_seq == 3


async def test_publish_revalidates_constructed_instances_and_freezes_nested_data(
    tmp_path: Path,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        malformed = ErrorReport.model_construct(
            source="invalid", message="x", agent_id=None, change_id=None, fatal=False
        )
        with pytest.raises(EventRejected):
            await store.publish(malformed)
        assert (await store.publish(report())).seq == 1


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_fake_review_requires_independent_explicit_authorization(
    tmp_path: Path, mode: Literal["live", "replay"]
) -> None:
    async with EventStore(tmp_path / "denied.sqlite", RUN, mode=mode) as store:  # pyright: ignore[reportArgumentType]
        with pytest.raises(EventRejected, match="fake review"):
            await store.publish(review())
        assert (await store.publish(report())).seq == 1
    async with EventStore(
        tmp_path / "allowed.sqlite", RUN, mode=mode, allow_fake_provider=True
    ) as store:  # pyright: ignore[reportArgumentType]
        assert (await store.publish(review())).seq == 1


async def test_reference_to_rejected_candidate_is_rejected_individually(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        request = resolution(
            decision="changes_requested",
            decided_by="cross_review",
            head_commit="head",
            comment="Fix",
            review_seq=1,
        )
        outcomes = await asyncio.gather(
            store.publish(review()),
            store.publish(request),
            store.publish(report()),
            return_exceptions=True,
        )
        assert isinstance(outcomes[0], EventRejected)
        assert isinstance(outcomes[1], EventRejected)
        assert isinstance(outcomes[2], StoredEvent) and outcomes[2].seq == 1


async def test_unexpected_writer_failure_ends_all_publishers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:

        async def broken(*_: object) -> None:
            raise RuntimeError("writer crashed")

        monkeypatch.setattr(store, "_commit", broken)
        async with asyncio.timeout(2):
            outcomes = await asyncio.gather(
                store.publish(report()), store.publish(report()), return_exceptions=True
            )
        assert all(isinstance(result, RuntimeError) for result in outcomes)


async def test_all_six_reserved_events_pass_through_the_store(tmp_path: Path) -> None:
    import json

    from pydantic import TypeAdapter

    from oversight_contracts.events import ServerEvent

    fixture = ROOT / "fixtures/contracts/reserved-1.4.json"
    rows = json.loads(fixture.read_text())
    async with EventStore(tmp_path / "reserved.sqlite", RUN, allow_fake_provider=True) as store:
        for row in rows:
            event = TypeAdapter(ServerEvent).validate_python(row)
            saved = await store.publish(event.payload)
            assert saved.seq == event.seq and saved.type == event.type


def adverse_review() -> CrossReview:
    return review().model_copy(
        update={
            "verdict": "changes_requested",
            "findings": [ReviewFinding(severity="SHOULD-FIX", text="Fix this")],
        }
    )


def adverse_request(seq: int) -> ChangeResolved:
    return resolution(
        decision="changes_requested",
        decided_by="cross_review",
        head_commit="head",
        comment="Fix this",
        review_seq=seq,
    )


@pytest.mark.parametrize("stored_count", [1, 128])
async def test_commit_parses_only_the_new_publication(tmp_path: Path, stored_count: int) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        for _ in range(stored_count):
            await store.publish(report())
        with patch.object(
            TypeAdapter, "validate_json", autospec=True, side_effect=TypeAdapter.validate_json
        ) as parse:
            assert (await store.publish(report())).seq == stored_count + 1
        # publish freezes the new payload once; commit never reparses the stored log.
        assert parse.call_count == 1


async def test_batch_references_see_accepted_candidates_and_never_rejected_ones(
    tmp_path: Path,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN, allow_fake_provider=True) as store:
        adverse = adverse_review()
        outcomes = await asyncio.gather(
            store.publish(adverse),
            store.publish(adverse_request(2)),  # self/future reference, rejected
            store.publish(adverse_request(1)),
            return_exceptions=True,
        )
        assert isinstance(outcomes[0], StoredEvent) and outcomes[0].seq == 1
        assert isinstance(outcomes[1], EventRejected)
        assert isinstance(outcomes[2], StoredEvent) and outcomes[2].seq == 2
        # The rejected request did not become a review in the committed index.
        with pytest.raises(EventRejected, match=r"requires change\.reviewed"):
            await store.publish(adverse_request(2))
        with pytest.raises(EventRejected, match="only one cross_review"):
            await store.publish(adverse_request(1))


async def test_sql_rollback_does_not_extend_the_committed_reference_index(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN, allow_fake_provider=True) as store:
        await store.publish(adverse_review())
        connection = sqlite3.connect(store.path)
        try:
            connection.execute(
                "CREATE TRIGGER fail_batch BEFORE INSERT ON events WHEN NEW.seq = 3 "
                "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
            )
            outcomes = await asyncio.gather(
                store.publish(adverse_review()),
                store.publish(adverse_request(2)),
                return_exceptions=True,
            )
            assert all(isinstance(result, sqlite3.IntegrityError) for result in outcomes)
            assert store.last_seq == 1
            assert [row.seq for row in read_store(store.path)] == [1]
            connection.execute("DROP TRIGGER fail_batch")
            with pytest.raises(EventRejected, match="absent or not earlier"):
                await store.publish(adverse_request(2))
            # The pre-transaction reference survived; the rolled-back seq is reusable.
            assert (await store.publish(adverse_request(1))).seq == 2
            with pytest.raises(EventRejected, match=r"requires change\.reviewed"):
                await store.publish(adverse_request(2))
        finally:
            connection.close()


async def test_closed_log_replays_into_a_fresh_reference_index(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    async with EventStore(path, RUN, allow_fake_provider=True) as store:
        await store.publish(adverse_review())
        await store.publish(adverse_request(1))
    # Existing run files remain read-only: reopen their envelopes and replay through admission.
    log = tmp_path / "reopened.jsonl"
    log.write_text("".join(row.envelope + "\n" for row in read_store(path)))
    async with EventStore(
        tmp_path / "replay.sqlite", RUN, mode="replay", allow_fake_provider=True
    ) as replay:
        await play(replay, read_jsonl(log), speed=0)
        with pytest.raises(EventRejected, match="only one cross_review"):
            await replay.publish(adverse_request(1))
        with pytest.raises(EventRejected, match=r"requires change\.reviewed"):
            await replay.publish(adverse_request(2))
    assert len(read_store(path)) == 2


async def test_store_rejects_second_review_of_the_same_change(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "cap.sqlite", RUN, allow_fake_provider=True) as store:
        await store.publish(adverse_review())
        await store.publish(adverse_request(1))
        second = adverse_review().model_copy(update={"revision": 2, "head_commit": "next"})
        seq = (await store.publish(second)).seq
        with pytest.raises(EventRejected, match="only one cross_review"):
            await store.publish(
                adverse_request(seq).model_copy(update={"revision": 2, "head_commit": "next"})
            )
        # The review_seq limit is independent of change_id/target reference validation.
        with pytest.raises(EventRejected, match="review_seq may have only one"):
            await store.publish(adverse_request(1).model_copy(update={"change_id": "change-2"}))
        assert store.last_seq == 3


async def test_jsonl_reader_refuses_duplicate_automatic_resolution(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN, allow_fake_provider=True) as store:
        await store.publish(adverse_review())
        request = await store.publish(adverse_request(1))
        rows = [row.envelope for row in read_store(store.path)]
    duplicate = json.loads(request.envelope)
    duplicate["seq"] = 3
    path = tmp_path / "duplicate.jsonl"
    path.write_text("\n".join([*rows, json.dumps(duplicate)]) + "\n")
    with pytest.raises(LogError, match="only one cross_review"):
        read_jsonl(path)
