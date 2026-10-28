"""Automatic admission makes all human decision routes return HTTP 409 until it settles."""

import asyncio
from pathlib import Path

import pytest
from decision_helpers import observed_body
from live_helpers import DEADLINE_S, live_server, live_settings, start
from revise_helpers import ready

from oversight.agents import (
    FAKE_MODEL,
    FAKE_MODEL_B,
    ModelReply,
    ReviewInputs,
    ReviewTests,
    Script,
    ScriptedModel,
    review_candidate,
)

pytestmark = pytest.mark.anyio


async def test_automatic_reservation_returns_409_to_human_routes(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as server:
        assert (await start(server)).status_code == 201
        await ready(server)
        manager = server.live._manager
        assert manager is not None
        capture = await manager.capture_review("change-1")
        path = "shop/pricing.py"
        model = ScriptedModel(
            Script(
                "review",
                [
                    ModelReply(
                        f"SHOULD-FIX {path}:2: Correct the calculation.\nVERDICT: request changes",
                        [],
                        model=FAKE_MODEL_B.model,
                    )
                ],
            ),
            FAKE_MODEL_B,
        )
        outcome = await review_candidate(
            model,
            capture.target,
            ReviewInputs(
                reviewer_model=FAKE_MODEL_B.model,
                summary="Review",
                diff="+return 3",
                files=(path,),
                tests=ReviewTests("passed", 1, 0, 0),
                cone=(),
            ),
            author=FAKE_MODEL,
        )
        server.store.allow_fake_provider = True
        review = await server.store.publish(outcome.review)
        async with server.live.workspace.locks(main=True):
            automatic = manager.request_cross_review(capture, outcome, review_seq=review.seq)
            for route in ("approve", "reject", "request-changes"):
                response = await server.client.post(
                    f"/changes/change-1/{route}",
                    json={
                        **observed_body(server),
                        "comment": "My decision",
                    },
                )
                assert response.status_code == 409, response.text
            assert not manager._agents["agent-1"].person_acted
        assert await automatic is not None
