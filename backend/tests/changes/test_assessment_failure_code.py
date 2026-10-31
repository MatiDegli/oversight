"""Assessment readiness failures retain their terminal code and reason."""

from unittest.mock import AsyncMock

import pytest
from test_assessment import settled, terminals
from test_spend_evidence import spend as spend

from oversight.changes import _spend
from oversight.changes._support import RulesFailure

pytestmark = pytest.mark.anyio


async def test_evaluate_preserves_readiness_failure_code(h, spend, monkeypatch):
    reason = "Injected spend readiness failure"
    monkeypatch.setattr(
        _spend, "ready", AsyncMock(side_effect=RulesFailure("spend_invalid", reason))
    )
    h.manager.run_ended("agent-1", None)
    await settled(h)
    errors = terminals(h)
    assert len(errors) == 1
    assert "code=spend_invalid;" in errors[0].message
    assert reason in errors[0].message
