"""Keep durability and accounting guarantees on their public documentation targets."""

from inspect import getdoc, getmodule

import pytest

from oversight.events import EventStore, fold_usage


@pytest.mark.parametrize(
    ("target", "guarantees"),
    [
        (
            getmodule(EventStore),
            (
                "Only the writer task assigns `seq` and `ts`",
                "a burst of events costs one commit",
                "An event reaches subscribers only after its transaction commits",
            ),
        ),
        (
            EventStore,
            (
                "`.runtime/runs/{run_id}.sqlite`",
                "A run's log is small, so subscribers read from the in-memory copy "
                "of what was committed.",
            ),
        ),
        (
            EventStore.publish,
            (
                "return it once committed, with its `seq` and `ts`",
                "`ts` defaults to the clock at commit time",
                "the replay player passes the recorded one",
            ),
        ),
        (
            fold_usage,
            (
                "Fold a complete, ordered, committed prefix starting at seq 1 (or an empty prefix)",
                "Reject missing/duplicate/out-of-order seqs and invalid costs with ValueError",
                "absent receipts or calls never recorded by a producer "
                "cannot be reconstructed here",
                "Round zero precedes confirmed changes_requested resolutions",
                "This is receipt order, not proof of the originating call's revision",
                "Proposal revisions, agent ids, model names and timing imply no attribution",
                "reviewer and decision calls remain combined under the existing review role",
            ),
        ),
    ],
    ids=["store-module", "store-class", "publish", "usage-fold"],
)
def test_public_docs_preserve_event_log_guarantees(
    target: object, guarantees: tuple[str, ...]
) -> None:
    documentation = getdoc(target)
    assert documentation is not None
    normalized = " ".join(documentation.split())
    for guarantee in guarantees:
        assert guarantee in normalized


def test_usage_module_keeps_accounting_details_on_the_fold() -> None:
    assert getdoc(getmodule(fold_usage)) == (
        "Pure accounting over a complete committed event prefix, with no decision authority."
    )
