"""A closed, machine-readable refusal line at each startup boundary."""

import contextlib
import sys
from collections.abc import Generator
from contextvars import ContextVar

CATEGORIES = frozenset(
    {"not-standalone-repo", "unsupported-location", "path-too-long", "previous-session-changed"}
)


# Uvicorn can convert an already reported lifespan failure into SystemExit. Share the
# same mutable attempt across its copied task contexts, without suppressing a later attempt.
_attempt: ContextVar[list[bool] | None] = ContextVar("startup_refusal_attempt", default=None)


@contextlib.contextmanager
def startup_refusals() -> Generator[None]:
    attempt = _attempt.get()
    token = None
    if attempt is None:
        attempt = [False]
        token = _attempt.set(attempt)
    try:
        yield
    except (Exception, SystemExit) as error:
        success = isinstance(error, SystemExit) and error.code in (None, 0)
        if not attempt[0] and not success:
            category = getattr(error, "refusal_category", "other")
            if category not in CATEGORIES:
                category = "other"
            print(f"OVERSIGHT_REFUSAL={category}", file=sys.stderr, flush=True)
            attempt[0] = True
        raise
    finally:
        if token is not None:
            _attempt.reset(token)
