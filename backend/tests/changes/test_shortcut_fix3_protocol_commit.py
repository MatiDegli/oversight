"""Inlined historical source fixtures; tests never invoke git."""

from oversight.changes import detect_shortcuts

NEW = (
    '"""The command routes of section 4.7: start an agent, approve or reject '
    "a change; 501 for the rest.\n\nA replay takes no commands: every command r"
    "oute answers 501 there, its body not parsed. Live, the\napp passes an obj"
    "ect that implements `Commands`; this module maps its errors to the contr"
    'act\'s.\n"""\n\nfrom collections.abc import Callable, Coroutine\nfrom typing '
    "import Annotated, Any, Protocol\n\nfrom fastapi import APIRouter, Body, Re"
    "sponse\n\nfrom oversight.api.errors import ApiError\nfrom oversight.changes"
    " import ChangeNotFound, WrongState\nfrom oversight_contracts.commands imp"
    "ort AgentCreated, ApproveChange, RejectChange, StartAgent\n\nNOT_BUILT: tu"
    'ple[tuple[str, str, str], ...] = (\n    ("POST", "/agents/{agent_id}/stop'
    '", "Stop"),\n    ("POST", "/changes/{change_id}/request-changes", "Reques'
    't changes"),\n    ("GET", "/impact", "The impact query"),\n    ("GET", "/f'
    'iles", "Reading files"),\n    ("PUT", "/files", "Saving files"),\n    ("PO'
    'ST", "/graph/rescan", "Rescanning the graph"),\n    ("POST", "/agents/{ag'
    'ent_id}/message", "Messages to an agent"),\n    ("POST", "/agents/{agent_'
    'id}/pause", "Pause"),\n    ("POST", "/agents/{agent_id}/resume", "Resume"'
    '),\n)\n"""Routes in the contract that this level does not build: 501, what'
    'ever the body."""\nLIVE_ONLY: tuple[tuple[str, str, str], ...] = (\n    ("'
    'POST", "/agents", "Starting an agent"),\n    ("POST", "/changes/{change_i'
    'd}/approve", "Approve"),\n    ("POST", "/changes/{change_id}/reject", "Re'
    'ject"),\n)\n\n\nclass Commands(Protocol):\n    """What the live app does for '
    "the command routes. `start` raises `ApiError` for a refused\n    start; `"
    "approve` and `reject` take the decision with no `await`, so a second one"
    ' raises\n    `WrongState` at once, and its result arrives as events."""\n\n'
    "    async def start(self, request: StartAgent) -> AgentCreated: ...\n\n   "
    " def approve(self, change_id: str, comment: str | None) -> None: ...\n\n  "
    "  def reject(self, change_id: str, comment: str | None) -> None: ...\n\n\nd"
    "ef _not_built(what: str, why: str) -> Callable[[], Coroutine[Any, Any, N"
    'one]]:\n    async def answer() -> None:\n        raise ApiError("not_imple'
    'mented", f"{what} {why}")\n\n    return answer\n\n\ndef _decide(\n    decision'
    ": Callable[[str, str | None], None], change_id: str, comment: str | None"
    "\n) -> None:\n    try:\n        decision(change_id, comment)\n    except Cha"
    'ngeNotFound:\n        raise ApiError("not_found", f"there is no change {c'
    'hange_id!r}") from None\n    except WrongState as error:\n        raise Ap'
    'iError("wrong_state", str(error)) from None\n\n\ndef add_command_routes(rou'
    "ter: APIRouter, commands: Commands | None) -> None:\n    for method, path"
    ", what in NOT_BUILT:\n        router.add_api_route(\n            path, _no"
    't_built(what, "is not built yet"), methods=[method], status_code=501\n   '
    "     )\n    if commands is None:\n        for method, path, what in LIVE_O"
    "NLY:\n            router.add_api_route(\n                path, _not_built("
    'what, "is not available in a replay"), methods=[method]\n            )\n  '
    '      return\n\n    @router.post("/agents", status_code=201)\n    async def'
    " start_agent(request: StartAgent) -> AgentCreated:  # pyright: ignore[re"
    "portUnusedFunction]\n        return await commands.start(request)\n\n    @r"
    'outer.post("/changes/{change_id}/approve", status_code=202, response_cla'
    "ss=Response)\n    async def approve(  # pyright: ignore[reportUnusedFunct"
    "ion]\n        change_id: str, body: Annotated[ApproveChange | None, Body("
    ")] = None\n    ) -> Response:\n        _decide(commands.approve, change_id"
    ", body.comment if body else None)\n        return Response(status_code=20"
    '2)\n\n    @router.post("/changes/{change_id}/reject", status_code=202, res'
    "ponse_class=Response)\n    async def reject(  # pyright: ignore[reportUnu"
    "sedFunction]\n        change_id: str, body: Annotated[RejectChange | None"
    ", Body()] = None\n    ) -> Response:\n        _decide(commands.reject, cha"
    "nge_id, body.comment if body else None)\n        return Response(status_c"
    "ode=202)\n"
)


def test_commit_5648808_protocol_is_clean() -> None:
    assert detect_shortcuts([("backend/src/oversight/api/commands.py", None, NEW)]) == []
