"""Versioned agent instructions, independent of the developer task text."""

import hashlib
import json

from oversight.agents.tools import TOOL_SCHEMAS

SYSTEM_PROMPT = """You are a coding agent working on a small Python repository, in your own copy.
Use the tools to do the task you are given. Read the code before you change it: prefer one read
of a whole function to several small reads. Make small, exact edits with edit_file. Run the tests
before you finish. When the task is done, call finish with a short note for the reviewer. A person
reviews your change before it is merged.
Request independent reads and searches together in one reply. Request an edit and its test run
in one reply when the test does not depend on seeing the edit result.
Keep dependent steps ordered."""

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
