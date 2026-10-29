"""Read D6's allowance without constructing a client or loading credentials."""

import ast
import inspect

from tools.eval.tasks import BenchError

from oversight.decision_config import configured_decision


def completion_limit() -> int:
    # D6 does not export a constant. Inspect the imported factory, never execute it.
    tree = ast.parse(inspect.getsource(configured_decision))
    values = [
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "DecisionClient"
        for keyword in node.keywords
        if keyword.arg == "max_output_tokens" and isinstance(keyword.value, ast.Constant)
    ]
    if len(values) != 1 or type(values[0]) is not int or values[0] <= 0:
        raise BenchError("D6 completion allowance is no longer one positive literal")
    return values[0]
