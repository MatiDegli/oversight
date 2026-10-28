"""Versioned agent instructions, independent of the developer task text."""

import hashlib
import json

from oversight.agents.tools import TOOL_SCHEMAS
from oversight.settings import harness_pack_enabled

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

SYSTEM_PROMPT = _PACK_SYSTEM_PROMPT if harness_pack_enabled() else _BASE_PROMPT

REVISION_PROMPT = """The reviewer has requested changes to the change you proposed.

Reviewer comment:
{comment}
{note}
Read a file again before editing it, even if you read or edited it earlier: the file may have \
changed since you last saw it. Make the requested changes, run the tests, and call finish with \
a short note for the reviewer."""

PROMPT_VERSION = (
    "agent-v1-"
    + hashlib.sha256(
        (SYSTEM_PROMPT + REVISION_PROMPT + json.dumps(TOOL_SCHEMAS, sort_keys=True)).encode()
    ).hexdigest()[:12]
)
"""A name and a hash of the system prompt and tool schemas, for `run.started`."""
