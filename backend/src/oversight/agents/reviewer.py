"""Unwired adapter from one reviewer reply to the frozen advisory review contract.

No publication, recovery turn, timer, ledger write or decision execution lives here. The runtime
will supply its own call scope and accounting in a later PR. A retained reply includes its usage
even when the provider returned no choices or the review is unusable.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from oversight.agents._crossreview import (
    MAX_DIFF_CHARS,
    Decision,
    Inputs,
    Review,
    build_prompt,
    decide,
    parse,
)
from oversight.agents.client import EmptyChoicesError, ModelAPIError, ModelClient, ModelReply
from oversight_contracts.events import ModelProfile
from oversight_contracts.review import ChangeTarget, CrossReview, ReviewFinding

REVIEW_MAX_OUTPUT_TOKENS = 12_288
"""Provisional reviewer budget; paid acceptance must measure both providers at this limit."""


@dataclass(frozen=True)
class ReviewOutcome:
    review: CrossReview
    decision: Decision
    reply: ModelReply | None
    """Raw identity and usage, retained even without a verdict. None means no usable response."""


async def review_candidate(
    client: ModelClient,
    target: ChangeTarget,
    inputs: Inputs,
    *,
    author: ModelProfile,
    author_reply_models: Sequence[str | None] = (),
    material_problems: Sequence[str] = (),
) -> ReviewOutcome:
    """Review captured material once, without touching proposals or the author's history.

    Only diagnostics about this review's material belong in material_problems. Missing facts
    from unrelated producers do not make an otherwise complete review incomplete.
    """
    profile = client.profile
    problems = list(material_problems)
    if author.model == profile.model:
        problems.append("reviewer_same: reviewer and author have the same model")
    if any(model is not None and model != author.model for model in author_reply_models):
        problems.append("review_invalid: reported author model differs from its profile")
    if inputs.reviewer_model != profile.model:
        problems.append("review_invalid: material names a different reviewer model")
    if len(inputs.diff) > MAX_DIFF_CHARS:
        problems.append(f"review_invalid: diff exceeds {MAX_DIFF_CHARS:,} characters; no call made")
    reply: ModelReply | None = None
    parsed = Review("invalid", (), (), profile.model)
    if not problems:
        try:
            reply = await client.complete([{"role": "user", "content": build_prompt(inputs)}], [])
        except EmptyChoicesError as error:
            reply = error.reply
            problems.append("review_empty: the API returned no choices")
        except ModelAPIError as error:
            problems.append(f"review_api_failed: {error}")
        if reply is not None:
            parsed, diagnostics = _read_reply(reply, inputs)
            problems.extend(diagnostics)
    problems.extend(parsed.problems)
    if problems:
        parsed = replace(parsed, verdict="invalid", problems=tuple(problems))
    verdict: Literal["clean", "changes_requested", "unknown"] = "unknown"
    if not problems:
        verdict = "clean" if parsed.verdict == "accept" else "changes_requested"
    review = CrossReview(
        **target.model_dump(),
        author_model=author.model,
        reviewer_model=profile.model,
        reviewer_provider=profile.provider,
        reasoning=profile.reasoning,
        verdict=verdict,
        findings=[
            ReviewFinding(severity=f.severity, text=f.text, path=f.path, line=f.line)
            for f in parsed.findings
        ],
        complete=not problems,
        problems=problems,
    )
    return ReviewOutcome(review, decide(parsed, 0), reply)


def _read_reply(reply: ModelReply, inputs: Inputs) -> tuple[Review, list[str]]:
    problems: list[str] = []
    if reply.model is not None and reply.model != inputs.reviewer_model:
        problems.append("review_invalid: reported reviewer model differs from its profile")
    if reply.finish_reason == "length":
        problems.append("review_truncated: output token limit reached")
    if reply.tool_calls:
        problems.append("review_invalid: unexpected tool calls in reviewer reply")
    if not reply.text or not reply.text.strip():
        problems.append("review_empty: empty reviewer reply")
    parsed = parse(reply.text or "", inputs.files, reviewer_model=inputs.reviewer_model)
    if parsed.verdict == "invalid":
        problems.append("review_invalid: invalid reviewer output")
    return parsed, problems
