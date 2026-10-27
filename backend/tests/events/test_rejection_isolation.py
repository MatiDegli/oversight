"""One bad producer cannot poison a writer batch or leave publishers awaiting forever."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest

from oversight.events import EventRejected, EventStore, StoredEvent
from oversight_contracts.events import ChangeResolved, ErrorReport
from oversight_contracts.review import CrossReview

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
