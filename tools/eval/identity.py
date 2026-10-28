"""Immutable experiment identity shared by durable reservations and results."""

from pydantic import BaseModel, ConfigDict, Field


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arm_id: str = Field(default="legacy", min_length=1, frozen=True)
    repetition: int = Field(default=1, ge=1, frozen=True)
    config_hash: str = Field(default="legacy", min_length=1, frozen=True)
    attempt_id: str = Field(default="legacy", min_length=1, frozen=True)
