"""Opt-in reviewer construction, deliberately unconnected to the live proposal lifecycle."""

from oversight.agents import (
    BASETEN_BASE_URL,
    META_BASE_URL,
    REVIEW_MAX_OUTPUT_TOKENS,
    ModelClient,
    OpenAIClient,
    ScriptedModel,
    load_script,
)
from oversight.settings import Settings
from oversight.setup import offered_profiles
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile


class ReviewerUnavailable(ValueError):
    """Configuration cannot supply a distinct, authenticated reviewer. No call was made."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code


def configured_reviewer(
    settings: Settings, author_slot: Slot, author: ModelProfile
) -> ModelClient | None:
    """A fresh client for the opposite slot's profile; never reuse an author client or script.

    Off returns before profile resolution, script I/O, SDK construction or any async work.
    The opposite profile can review without an active agent in that slot.
    """
    if not settings.cross_review:
        return None
    profiles = offered_profiles(settings)
    profile = profiles.get("B" if author_slot == "A" else "A")
    if profile is None:
        raise ReviewerUnavailable("reviewer_missing", "The opposite slot has no reviewer profile.")
    if profile.model == author.model:
        raise ReviewerUnavailable("reviewer_same", "Reviewer and author have the same model.")
    if profile.provider == "fake":
        if author.provider != "fake" or any(p.provider != "fake" for p in profiles.values()):
            raise ReviewerUnavailable(
                "reviewer_credentials", "Fake review requires all-fake profiles."
            )
        script = (
            settings.reviewer_fake_script
            if profile.id == "fake"
            else settings.reviewer_fake_b_script
        )
        if script is None:
            raise ReviewerUnavailable(
                "reviewer_credentials", "The reviewer's fake script is missing."
            )
        return ScriptedModel(load_script(script, profile), profile)
    key = settings.meta_api_key if profile.provider == "meta" else settings.baseten_api_key
    if not key or any(char.isspace() for char in key):
        raise ReviewerUnavailable(
            "reviewer_credentials", "The reviewer credential is missing or invalid."
        )
    # Explicitly set low for each reviewer, independently of author defaults or client history.
    profile = profile.model_copy(update={"reasoning": "low"})
    return OpenAIClient(
        profile,
        base_url=META_BASE_URL if profile.provider == "meta" else BASETEN_BASE_URL,
        api_key=key,
        max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
    )
