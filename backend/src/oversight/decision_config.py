"""Independent Spark Open decision profile; no fake selection through configuration."""

from oversight.agents import BASETEN_BASE_URL, MUSE_SPARK_OPEN, DecisionClient, DecisionUnavailable
from oversight.settings import Settings


def configured_decision(settings: Settings) -> DecisionClient:
    """Construct only on an intentional decision call, after runtime spending admission."""
    if settings.decision_model != MUSE_SPARK_OPEN.id:
        raise DecisionUnavailable("profile")
    key = settings.baseten_api_key
    if not key or any(char.isspace() for char in key):
        raise DecisionUnavailable("credentials")
    return DecisionClient(
        MUSE_SPARK_OPEN,
        base_url=BASETEN_BASE_URL,
        api_key=key,
        # Reasoning tokens share the completion limit; the output itself is a tiny enum.
        max_output_tokens=4096,
    )
