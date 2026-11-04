"""Adapt a reviewer conversation to the frozen advisory review contract.

One parser correction may continue the independent reviewer history. The runtime supplies the
deadline, spending admission and accounted client; publication and decisions remain outside.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from oversight.agents._crossreview import (
    MAX_DIFF_CHARS,
    Decision,
    Inputs,
    Review,
    build_correction_message,
    build_prompt,
    decide,
    parse,
)
from oversight.agents.client import (
    EmptyChoicesError,
    Message,
    ModelAPIError,
    ModelClient,
    ModelReply,
)
from oversight_contracts.events import ModelProfile
from oversight_contracts.review import ChangeTarget, CrossReview, ReviewFinding

REVIEW_MAX_OUTPUT_TOKENS = 12_288
"""Provisional reviewer budget; paid acceptance must measure both providers at this limit."""
MIN_REASK_SECONDS = 20
_INVALID_OUTPUT = "review_invalid: invalid reviewer output"


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
    deadline: float | None = None,
    check_spending: Callable[[], None] | None = None,
) -> ReviewOutcome:
    """Review captured material with at most one formatting correction in its own history.

    Only diagnostics about this review's material belong in material_problems. Missing facts
    from unrelated producers do not make an otherwise complete review incomplete.
    """
    profile = client.profile
    problems: list[str] = list(material_problems)
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
        messages: list[Message] = [{"role": "user", "content": build_prompt(inputs)}]
        for attempt in range(2):
            reply = None
            parsed = Review("invalid", (), (), profile.model)
            problems = []
            try:
                reply = await client.complete(messages, [])
            except EmptyChoicesError as error:
                reply = error.reply
                problems.append("review_empty: the API returned no choices")
            except ModelAPIError as error:
                problems.append(f"review_api_failed: {error}")
                break
            parsed, diagnostics = _read_reply(reply, inputs)
            problems.extend(diagnostics)
            if attempt or parsed.verdict != "invalid" or problems != [_INVALID_OUTPUT]:
                break
            messages.extend(
                [
                    {"role": "assistant", "content": reply.text},
                    {
                        "role": "user",
                        "content": build_correction_message(reply.text or "", parsed.problems),
                    },
                ]
            )
            if (
                deadline is not None
                and deadline - asyncio.get_running_loop().time() < MIN_REASK_SECONDS
            ):
                parsed = replace(
                    parsed,
                    problems=(
                        *parsed.problems,
                        "There was not enough time left to ask the reviewer again.",
                    ),
                )
                break
            if check_spending is not None:
                check_spending()
    return _outcome(target, author, profile, parsed, problems, reply)


def _outcome(
    target: ChangeTarget,
    author: ModelProfile,
    profile: ModelProfile,
    parsed: Review,
    problems: list[str],
    reply: ModelReply | None,
) -> ReviewOutcome:
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
        problems.append(_INVALID_OUTPUT)
    return parsed, problems
