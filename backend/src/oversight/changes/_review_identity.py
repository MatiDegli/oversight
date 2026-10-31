"""Common complete typed identities across the two private observation formats."""

import json
from typing import Any, cast


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _literal(value: Any) -> list[Any]:
    kind = cast(type[Any], type(value))
    if value is None:
        return ["none"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        return ["int", hex(value)]
    if kind is float:
        return ["float", value.hex()]
    if kind is str:
        return ["str", value]
    if kind is bytes:
        return ["bytes", value.hex()]
    if kind is range:
        return ["range", hex(value.start), hex(value.stop), hex(value.step)]
    if kind in (tuple, list):
        return [kind.__name__, [_literal(item) for item in value]]
    if kind is dict:
        return ["dict", [[_literal(key), _literal(item)] for key, item in value.items()]]
    raise ValueError("unsupported typed input")


def bound_inputs(arguments: dict[str, Any]) -> str | None:
    try:
        return canonical(_literal(dict(sorted(arguments.items()))))
    except ValueError:
        return None


def _worker_value(value: Any) -> list[Any]:
    tag = value[0]
    if tag == "none":
        return [tag]
    if tag in ("int", "bool", "float"):
        return [tag, value[1]]
    if tag in ("str", "bytes"):
        if len(value) == 4 and value[-1] != "complete":
            raise ValueError("truncated typed value")
        return [tag, value[1]]
    if tag in ("tuple", "list"):
        if len(value) == 4 and value[-1] != "complete":
            raise ValueError("truncated typed value")
        return [tag, [_worker_value(item) for item in value[1]]]
    if tag == "dict":
        return [tag, [[_worker_value(key), _worker_value(item)] for key, item in value[1]]]
    raise ValueError("incomplete typed display")


def direct_result(result: dict[str, Any]) -> str | None:
    """Only complete value displays share an identity with caller value snapshots.

    Exceptions have different observation surfaces (message versus typed args), so keep
    their full fingerprint identity rather than claiming an equality from display text.
    """
    if "state" in result:
        try:
            state = [
                [tag, [[name, _worker_value(value)] for name, value in result["state"][tag]]]
                for tag in ("stored", "properties")
            ]
            value = (
                ["raised", result.get("result_fingerprint", result.get("fingerprint", ""))]
                if result["kind"] == "raised"
                else ["value", _worker_value(result["value"])]
            )
            return canonical([*value, ["state", state]])
        except (ValueError, TypeError, IndexError, KeyError):
            return None
    if result["kind"] == "raised":
        return f"raised:{result.get('fingerprint', '')}"
    try:
        return canonical(["value", _worker_value(result["value"])])
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def caller_identity(values: tuple[str, str, str]) -> tuple[str, str, str]:
    try:
        inputs = json.loads(values[0])
        if inputs[0] == "dict":

            def key(pair: list[Any]) -> str:
                return canonical(pair[0])

            inputs[1].sort(key=key)
        return canonical(inputs), canonical(json.loads(values[1])), canonical(json.loads(values[2]))
    except (ValueError, TypeError, IndexError, KeyError):
        # Production reports have already validated their typed JSON. Synthetic/legacy
        # observations still retain their full identities, never a display prefix.
        return values
