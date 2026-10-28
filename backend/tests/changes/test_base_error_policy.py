"""Real base errors cannot certify the added-test merge rule."""

from dataclasses import replace

import pytest
from changes_helpers import Harness
from test_policy_profiles import PROFILE, policy_inputs
from test_probes_wiring import add_test

from oversight.changes._policy import assess, merges_alone
from oversight_contracts.events import SuiteExecution
from oversight_contracts.review import ChangeFacts


@pytest.fixture
def session_c() -> bool:
    return True


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["collection", "fixture", "assertion"])
async def test_base_error_is_unknown_but_assertion_proves_failure(h: Harness, failure: str):
    await h.open(1)
    if failure == "collection":
        assert await h.edit(1, "shop/mod.py", "return 1", "return 1\n\n\nNEW = 1")
        body = "from shop.mod import NEW\ndef test_added():\n    assert NEW == 1\n"
    elif failure == "fixture":
        assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
        body = (
            "import pytest\n@pytest.fixture\ndef ready():\n"
            "    if value() == 1:\n        raise RuntimeError('setup unavailable')\n"
            "def test_added(ready):\n    assert value() == 3\n"
        )
    else:
        assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
        body = "def test_added():\n    assert value() == 3\n"
    await add_test(h, body)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    [probe] = facts.added_tests
    assert probe.result == ("fails_before_passes_after" if failure == "assertion" else "unknown")
    before = next(e for e in h.payloads(SuiteExecution) if e.phase == "base_probe")
    assert any(
        c.outcome == ("failed" if failure == "assertion" else "error")
        for c in before.suite.cases or []
    )
    if failure != "assertion":
        assert any("setup or import error" in problem for problem in facts.problems)
    from oversight.changes._facts import AddedTestsInventory

    inputs = replace(
        policy_inputs(),
        added_tests=(probe,),
        added_tests_inventory=AddedTestsInventory(test_ids=(probe.test_id,), complete=True),
    )
    assessment = assess(inputs, PROFILE)
    rule = next(r for r in assessment.rules if r.rule == "added_tests_fail_before")
    assert rule.result == ("pass" if failure == "assertion" else "unknown")
    assert merges_alone(assessment) is (failure == "assertion")
