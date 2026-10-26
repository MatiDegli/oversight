"""The base model, identifiers and shared literals (docs/proposal.md, sections 4.1 and 4.2).

Each alias is a named type, so the JSON Schema gives it a definition and the TypeScript types keep
its name.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints


class ContractModel(BaseModel):
    """Frozen, and forbids extra fields: the schema says `additionalProperties: false`.

    Fields with a default are still required in the serialization schema, so in events and
    responses every key is always present. A field's docstring becomes its description in the
    schema and its comment in the TypeScript types.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_serialization_defaults_required=True,
        use_attribute_docstrings=True,
    )


type RunId = Annotated[str, StringConstraints(pattern=r"^run-\d{8}-\d{6}$")]
"""`run-` and the UTC start time: `run-20261027-140312`."""

type AgentId = Annotated[str, StringConstraints(pattern=r"^agent-[1-9]\d*$")]
"""`agent-` and a counter per run: `agent-1`."""

type ChangeId = Annotated[str, StringConstraints(pattern=r"^change-[1-9]\d*$")]
"""`change-` and a counter per run, one change per agent: `change-1`."""

type NodeId = str
"""A module's dotted path (`shop.pricing`), or the module, `:` and a qualified name
(`shop.models:Product.total`), with `#n` from a name's second definition on. Never a line number."""

type EdgeId = str
"""Kind, `:`, source, `->`, target: `calls:shop.orders:create_order->shop.pricing:compute_price`."""

type WorkspacePath = str
"""Relative to the workspace root, with forward slashes and no `.` or `..` segments; never absolute
and never inside `.git/`. A worktree uses the same paths as main."""

type Timestamp = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
]
"""UTC with millisecond precision and a `Z`: `2026-10-27T14:03:12.345Z`."""

type Slot = Literal["A", "B"]
"""The UI maps a slot to its color."""

type FileStatus = Literal["added", "modified", "deleted"]
