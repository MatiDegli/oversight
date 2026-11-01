"""Method exclusions keep private diagnostics inside the fixed reason allowlist."""

import ast
import inspect

import pytest

from oversight.changes import _behavior_methods
from oversight.changes._behavior import BehaviorDiff
from oversight.changes._behavior_publish import capture_behavior, project_behavior
from oversight.changes._behavior_reasons import NOT_PROBED_REASONS
from oversight.changes._shortcuts_common import Source


@pytest.mark.parametrize(
    "signature",
    ["change(self, *args)", "change(self, **kwargs)", "__init__(self, *args)"],
)
def test_variadic_method_signatures_publish_signature_reason(signature):
    from test_behavior_methods import PLAIN, prepared

    original = (
        "__init__(self, value=1)"
        if signature.startswith("__init__")
        else ("change(self, amount: int = 0)")
    )
    source = PLAIN.replace(original, signature)
    _, generated, found = prepared(source, qualified="Box.change")
    assert not generated
    [published] = (
        project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1) or []
    )
    assert published.not_probed == "signature not supported"
    assert found[0].reason == "variadic signature"


def test_duplicate_dataclass_field_diagnostic_becomes_unsupported_signature():
    from test_behavior_methods import prepared

    source = """from dataclasses import dataclass
@dataclass
class Box:
    value: int = 1
    value: int = 2
    def change(self): return None
"""
    parsed = Source(source)
    cls = next(node for node in parsed.tree.body if isinstance(node, ast.ClassDef))
    # The fixture reaches inspect.Signature, rather than an earlier dataclass exclusion.
    with pytest.raises(ValueError, match="duplicate parameter name"):
        _behavior_methods._constructor(parsed, cls)

    _, generated, found = prepared(source, qualified="Box.change")
    assert not generated
    assert found[0].reason == "unsupported signature"
    [published] = (
        project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1) or []
    )
    assert published.not_probed == "signature not supported"
    assert "duplicate" not in (found[0].reason or "")
    assert "duplicate" not in (published.not_probed or "")


def test_literal_method_value_errors_belong_to_reason_allowlist():
    tree = ast.parse(inspect.getsource(_behavior_methods))
    reasons = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ValueError"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    assert len(reasons) >= 10
    assert set(reasons) <= NOT_PROBED_REASONS.keys()
