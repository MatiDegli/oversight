"""Iterator observation is selected only by admitted, directly list-consumed test calls."""

import inspect
from dataclasses import replace

from oversight.changes._behavior_inputs import (
    BehaviorInputs,
    binding_arguments,
    calls,
    literal_text,
)
from oversight.changes._behavior_objects import invocation
from oversight.changes._behavior_sources import Sources

OBSERVATION_TAG = "oversight.iterator_items.v1"


def observation_call(literal: str, consume_iterator: bool) -> str:
    return literal_text((OBSERVATION_TAG, literal)) if consume_iterator else literal


def observe_inputs(
    module: str,
    name: str,
    caches: list[Sources],
    signatures: list[inspect.Signature],
    generated: BehaviorInputs,
) -> BehaviorInputs:
    if not generated.inputs:
        return generated
    for side, (cache, sig) in enumerate(zip(caches, signatures, strict=True)):
        accepted = {item.before if side == 0 else item.after for item in generated.inputs}
        for args, kwargs in calls(
            cache.files, module, name, cache.deadline, objects=True, cache=cache, consumed_only=True
        ):
            try:
                bound = sig.bind(*args, **kwargs)
                normalized = invocation(binding_arguments(sig, dict(bound.arguments)))
            except (TypeError, ValueError):
                continue
            if normalized in accepted:
                return replace(generated, consume_iterator=True)
    return generated
