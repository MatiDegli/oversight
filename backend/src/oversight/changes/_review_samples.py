"""Detached per-row comparison witnesses; never infer them from public aggregate counts."""

from dataclasses import dataclass

from oversight_contracts.review import BehaviorDiffRow


@dataclass(frozen=True)
class OutcomeKind:
    before_exception: str | None = None
    after_exception: str | None = None

    @property
    def description(self) -> str:
        if self.before_exception is None:
            return (
                "Returns a different value"
                if self.after_exception is None
                else f"Now raises {self.after_exception}"
            )
        if self.after_exception is None:
            return f"No longer raises {self.before_exception}"
        return f"Raises {self.after_exception} instead of {self.before_exception}"


def sample_key(
    values: tuple[str, str, str], node_id: str = ""
) -> tuple[int, int, tuple[str, str, str], str]:
    sizes = [len(value.encode("utf-8")) for value in values]
    return sizes[0], sum(sizes), values, node_id


@dataclass(frozen=True)
class Observation:
    node_id: str
    values: tuple[str, str, str]
    identity: tuple[str, str, str]
    excluded: str | None = None
    group: OutcomeKind | None = None
    coverage: int = 1
    differing: int = 1

    @property
    def key(self) -> tuple[int, int, tuple[str, str, str], str]:
        return sample_key(self.values, self.node_id)

    def question(self, base: str) -> str:
        if self.group is None:
            return base
        return (
            f"{base} {self.group.description}, "
            f"for {self.coverage} of {self.differing} differing inputs."
        )

    def problem(self, reason: str) -> str:
        count = f"; {self.coverage} differing inputs unclassified" if self.group else ""
        return f"review cards: {self.node_id}: {reason}{count}"

    def example(self) -> BehaviorDiffRow:
        return BehaviorDiffRow(inputs=self.values[0], before=self.values[1], after=self.values[2])


@dataclass(frozen=True)
class Selection:
    samples: tuple[Observation, ...]
    problems: tuple[str, ...]


def select(observations: tuple[Observation, ...], limit: int) -> Selection:
    """One minimum per direct group; callers retain their existing per-node selection."""
    unique: dict[tuple[str, tuple[str, str, str]], Observation] = {}
    problems: list[str] = []
    for sample in observations:
        if sample.excluded:
            problems.append(sample.problem(sample.excluded))
            continue
        identity = sample.node_id, sample.identity
        previous = unique.get(identity)
        if (
            previous is None
            or (sample.group is not None and previous.group is None)
            or (sample.group == previous.group and sample.key < previous.key)
        ):
            unique[identity] = sample
    minima: dict[tuple[str, OutcomeKind | None], Observation] = {}
    for sample in sorted(unique.values(), key=lambda item: item.key):
        minima.setdefault((sample.node_id, sample.group), sample)
    ordered = sorted(minima.values(), key=lambda item: item.key)
    unclassified = sum(
        sample.coverage
        for sample in unique.values()
        if sample.group is None and minima[(sample.node_id, None)] != sample
    )
    if unclassified:
        problems.append(f"review cards: {unclassified} other differences unclassified")
    if len(ordered) > limit:
        category = "groups" if any(sample.group for sample in ordered) else "nodes"
        count = sum(sample.coverage for sample in ordered[limit:])
        problems.append(
            f"review cards: {len(ordered) - limit} {category} omitted by classification limit; "
            f"{count} differing inputs unclassified"
        )
    return Selection(tuple(ordered[:limit]), tuple(dict.fromkeys(problems)))
