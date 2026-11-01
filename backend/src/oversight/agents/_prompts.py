"""Versioned agent instructions, independent of the developer task text."""

import hashlib
import json

from oversight.agents._lead_tools import LEAD_TOOL_SCHEMAS
from oversight.agents.tools import TOOL_SCHEMAS

_BASE_PROMPT = """You are a coding agent working on a small Python repository, in your own copy.
Use the tools to do the task you are given. Read the code before you change it: prefer one read
of a whole function to several small reads. Make small, exact edits with edit_file. Run the tests
before you finish. When the task is done, call finish with a short note for the reviewer. A person
reviews your change before it is merged.
Request independent reads and searches together in one reply. Request an edit and its test run
in one reply when the test does not depend on seeing the edit result.
Keep dependent steps ordered."""

_PACK_SYSTEM_PROMPT = """You are a coding agent in your own copy of a small Python repository.
Follow one path led by evidence: read the relevant code, form one hypothesis, make a small exact
edit, then run the tests. Read whole functions when practical. Use the same tools to inspect a
failure and correct that hypothesis. Keep dependent steps ordered.
Stop when the requested change is implemented and tested, or evidence shows you cannot proceed
within the limits. Call finish with a short note on the change, tests and any remaining blocker.
A person reviews the change before it is merged."""


def system_prompt(harness_pack: bool = False) -> str:
    return _PACK_SYSTEM_PROMPT if harness_pack else _BASE_PROMPT


SYSTEM_PROMPT = system_prompt()

REVISION_PROMPT = """The reviewer has requested changes to the change you proposed.

Reviewer comment:
{comment}
{note}
Read a file again before editing it, even if you read or edited it earlier: the file may have \
changed since you last saw it. Make the requested changes, run the tests, and call finish with \
a short note for the reviewer."""


PLAN_PROMPT = """Write the single subtask for the person's task in a small Python repository.
The next user message is the person's task verbatim. The final message lists main's modules
and top-level names at the plan's graph version; any truncation is stated there.
Preserve the person's intent and constraints. Do not split the task or invent requirements.
Return exactly one JSON object with exactly one key: {"task": "the subtask text"}.
The task must be nonempty and at most 4,000 characters. No tools, markdown or other text.
The server computes all plan metadata, zones, dependencies and assignments."""


LEAD_PROMPT = """You coordinate coding agents on a small Python repository.
You never edit code and never decide on changes. Read main with the tools: graph for modules and \
top-level names, list_files,
read_file for numbered lines, and queue for work already queued. Split the person's task into at
most 3 plans. Each plan has 1 to 6 subtasks. Each subtask is a complete instruction for one
coding agent, at most 2,000 characters. Use depends_on (zero-based subtask indexes) when a
subtask needs another one merged first. Subtasks without depends_on may run at the same time,
and nothing checks that they are independent, so give each one its own files where you can.
Submit each plan with one request_plan call. A person decides every plan: you cannot start,
queue, approve or reject anything. Call finish once your plans are requested. Do not wait for
agents."""


HISTORY_STUB = "[Lines {first}-{last} of {path}: a later read of this path covers these lines.]"
HISTORY_LOW_WATER = 0.75
HISTORY_REARM = 0.25
HISTORY_PROTECTED_CALLS = 6


def prompt_version(harness_pack: bool = False, history_prune_tokens: int | None = None) -> str:
    """A hash of the selected system, revision, plan and lead prompts, and both tool tables."""
    policy = ""
    if history_prune_tokens is not None:
        policy = json.dumps(
            [
                HISTORY_STUB,
                history_prune_tokens,
                HISTORY_LOW_WATER,
                HISTORY_REARM,
                HISTORY_PROTECTED_CALLS,
            ]
        )
    return (
        "agent-v1-"
        + hashlib.sha256(
            (
                system_prompt(harness_pack)
                + REVISION_PROMPT
                + PLAN_PROMPT
                + LEAD_PROMPT
                + json.dumps(TOOL_SCHEMAS, sort_keys=True)
                + json.dumps(LEAD_TOOL_SCHEMAS, sort_keys=True)
                + policy
            ).encode()
        ).hexdigest()[:12]
    )


PROMPT_VERSION = prompt_version()
"""The baseline version; configured runs select their version explicitly."""
