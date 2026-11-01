"""The built frontend, served by FastAPI for the demo: one origin, no Vite.

The UI answers every GET that no API route took. `/` and any other path that is not an API path
and does not name a file get `index.html`, so a client-side route survives a reload. A file of
the build is served as it is: hashed assets under `/assets/` with a year's cache, everything else,
`index.html` included, with `no-cache`. A path that names a file the build lacks, `/assets/x.js`
or `/favicon.ico`, is the API's `not_found`: a script request must never be answered with HTML.

The UI route steps aside for the API's own paths (`API_PATHS`, the list in the frontend's
vite.config.ts, which a test keeps equal): an unknown API path is the API's 404, and a wrong
method on a real one its 405, never `index.html`. No compression is added anywhere, so `/events`
reaches the browser as it is written.
"""

import ntpath
import sys
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from starlette.routing import Match, Route
from starlette.types import Scope

from oversight.api.errors import error_response

API_PATHS = frozenset(
    {
        "agents",
        "changes",
        "events",
        "files",
        "graph",
        "grants",
        "health",
        "impact",
        "merge-mode",
        "plans",
        "tree",
        "history",
        "queue",
        "leads",
        "dictation",
        "usage",
    }
)
"""The first path segment of every API route: what the UI never answers."""
INDEX = "index.html"
ASSETS = "assets"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
}
"""The types a browser enforces (module scripts, style sheets). The system's mime table decides
the rest, and a machine with an odd one must not break the build."""


class _UiRoute(Route):
    """The catch-all GET route, which matches no API path: the router then answers those itself."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        first = str(scope["path"]).lstrip("/").partition("/")[0]
        if first in API_PATHS:
            return Match.NONE, {}
        return super().matches(scope)


def _safe_nt_relative(relative: str) -> bool:
    """Reject Windows path syntax before any filesystem access, including SPA fallback."""
    if not relative or "\\" in relative or ":" in relative or ntpath.splitdrive(relative)[0]:
        return False
    for part in relative.split("/"):
        if part in ("", ".", "..") or part.endswith((".", " ")):
            return False
        if any(ord(char) < 32 or char in '<>"|?*' for char in part):
            return False
        # Twin of workspace._platform._reserved_nt_segment; private-workspace forbids importing it.
        stem = part.partition(".")[0].rstrip(" ").upper()
        if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³"
        ):
            return False
    return True


def _file_under(root: Path, relative: str) -> Path | None:
    """The file `relative` names inside `root`, or `None`: missing, a directory, or a path that
    leaves `root` (`..`, or a link out of it)."""
    if sys.platform == "win32" and not _safe_nt_relative(relative):
        return None
    try:
        target = (root / relative).resolve()
    # A NUL byte is ValueError; a loop of links is OSError, and RuntimeError on Python 3.12.
    except (OSError, ValueError, RuntimeError):
        return None
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def _respond(target: Path, root: Path) -> FileResponse:
    in_assets = target.relative_to(root).parts[0] == ASSETS
    return FileResponse(
        target,
        media_type=MEDIA_TYPES.get(target.suffix),
        headers={"Cache-Control": IMMUTABLE if in_assets else REVALIDATE},
    )


def add_static(app: FastAPI, dist: Path) -> bool:
    """Serve the build in `dist` after the app's own routes. False, and nothing added, without
    `dist/index.html`: a source checkout that was never built has no UI to serve."""
    root = dist.resolve()
    if not (root / INDEX).is_file():
        return False

    def serve(request: Request) -> Response:
        relative = request.path_params["path"]
        if sys.platform == "win32" and relative and not _safe_nt_relative(relative):
            return error_response("not_found", "no such resource")
        target = _file_under(root, relative or INDEX)
        if target is not None:
            return _respond(target, root)
        looks_like_file = relative.partition("/")[0] == ASSETS or "." in relative.rpartition("/")[2]
        if sys.platform == "win32" and relative.partition("/")[0].casefold() == ASSETS:
            looks_like_file = True
        index = _file_under(root, INDEX)
        if looks_like_file or index is None:
            return error_response("not_found", "no such resource")
        return _respond(index, root)

    # A sync endpoint runs in a worker thread: its file system calls never block the event loop.
    app.router.routes.append(_UiRoute("/{path:path}", serve, methods=["GET"]))
    return True
