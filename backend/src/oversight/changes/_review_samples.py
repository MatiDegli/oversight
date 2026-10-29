"""Detached per-row comparison witnesses; never infer them from public aggregate counts."""

from dataclasses import dataclass

from oversight_contracts.review import BehaviorDiffRow


@dataclass(frozen=True)
class Observation:
    node_id: str
    values: tuple[str, str, str]
    identity: tuple[str, str, str]
    excluded: str | None = None

    @property
    def key(self) -> tuple[int, int, tuple[str, str, str], str]:
        sizes = [len(value.encode("utf-8")) for value in self.values]
        return sizes[0], sum(sizes), self.values, self.node_id

    def example(self) -> BehaviorDiffRow:
        return BehaviorDiffRow(inputs=self.values[0], before=self.values[1], after=self.values[2])


@dataclass(frozen=True)
class Selection:
    samples: tuple[Observation, ...]
    problems: tuple[str, ...]


def select(observations: tuple[Observation, ...], limit: int) -> Selection:
    """One observed minimum per node, with explicit exclusions and discarded differences."""
    unique: dict[tuple[str, tuple[str, str, str]], Observation] = {}
    problems: list[str] = []
    for sample in observations:
        if sample.excluded:
            problems.append(f"review cards: {sample.node_id}: {sample.excluded}")
            continue
        identity = sample.node_id, sample.identity
        previous = unique.get(identity)
        if previous is None or sample.key < previous.key:
            unique[identity] = sample
    minima: dict[str, Observation] = {}
    for sample in sorted(unique.values(), key=lambda item: item.key):
        minima.setdefault(sample.node_id, sample)
    ordered = sorted(minima.values(), key=lambda item: item.key)
    if len(unique) > len(minima):
        problems.append(f"review cards: {len(unique) - len(minima)} other differences unclassified")
    if len(ordered) > limit:
        problems.append(
            f"review cards: {len(ordered) - limit} nodes omitted by classification limit"
        )
    return Selection(tuple(ordered[:limit]), tuple(dict.fromkeys(problems)))
