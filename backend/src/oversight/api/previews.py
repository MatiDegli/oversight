"""Dormant person-owned preview routes; private records never become response dictionaries."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from oversight.api.errors import ApiError
from oversight.workspace import IntegrityError, PreviewError, PreviewManager, PreviewStatusRecord
from oversight_contracts.commands import ErrorCode
from oversight_contracts.previews import (
    PreviewCreated,
    PreviewDetails,
    PreviewId,
    PreviewStatus,
    PreviewTokenRequest,
    StartPreview,
)

_ID = TypeAdapter(PreviewId)
INTERNAL_MESSAGE = "Live preview request failed; closure must be verified before continuing."


class _PreviewRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await handler(request)
            except ApiError:
                raise
            except RequestValidationError:
                raise ApiError("invalid_request", "Live preview request is invalid.") from None
            except Exception:
                # Consume unexpected projection/serialization failures before ASGI can log
                # application-controlled values in their exceptions or validation inputs.
                raise ApiError("internal", INTERNAL_MESSAGE) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return private


def public_status(record: PreviewStatusRecord) -> PreviewStatus:
    return PreviewStatus(
        preview_id=record.preview_id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        change_id=record.change_id,
        revision=record.revision,
        head_commit=record.head_commit,
        state=record.state,
        reason=record.reason,
    )


async def _call[T](operation: Callable[[], Awaitable[T]]) -> T:
    try:
        return await operation()
    except PreviewError as error:
        raise ApiError(
            cast(ErrorCode, error.code),
            str(error),
            missing=[] if error.code == "not_configured" else None,
        ) from None
    except IntegrityError:
        raise ApiError(
            "wrong_state", "Live preview metadata verification refused this operation."
        ) from None
    except Exception:
        raise ApiError("internal", INTERNAL_MESSAGE) from None


def add_preview_routes(router: APIRouter, previews: PreviewManager | None) -> None:
    routes = APIRouter(route_class=_PreviewRoute)

    def live() -> PreviewManager:
        if previews is None:
            raise ApiError("not_implemented", "Live preview is unavailable in replay.")
        return previews

    def identifier(value: str) -> str:
        try:
            return _ID.validate_python(value)
        except ValidationError:
            raise ApiError("invalid_request", "Live preview request is invalid.") from None

    async def body[TModel: StartPreview | PreviewTokenRequest](
        request: Request, model: type[TModel]
    ) -> TModel:
        try:
            return model.model_validate(await request.json(), strict=True)
        except (ValueError, ValidationError):
            raise ApiError("invalid_request", "Live preview request is invalid.") from None

    @routes.post("/previews", status_code=202)
    async def start(request: Request) -> PreviewCreated:  # pyright: ignore[reportUnusedFunction]
        # Replay refusal precedes parsing, identifier lookup and all localhost access.
        manager = live()
        value = await body(request, StartPreview)
        record = await _call(
            lambda: manager.start(
                run_id=value.run_id,
                agent_id=value.agent_id,
                revision=value.revision,
                head_commit=value.head_commit,
                parent_authority=request.headers["host"],
            )
        )
        return PreviewCreated(preview_id=record.preview_id, lease_token=record.lease_token)

    @routes.get("/previews/{preview_id}")
    async def details(preview_id: str, request: Request) -> PreviewDetails:  # pyright: ignore[reportUnusedFunction]
        manager = live()
        target = identifier(preview_id)
        record = await _call(lambda: manager.get(target, parent_authority=request.headers["host"]))
        return PreviewDetails(
            status=public_status(record), url=record.url if record.state == "ready" else None
        )

    async def token_operation(request: Request, preview_id: str, *, stop: bool) -> PreviewStatus:
        manager = live()
        target = identifier(preview_id)
        value = await body(request, PreviewTokenRequest)
        operation = manager.stop if stop else manager.renew
        return public_status(await _call(lambda: operation(target, value.lease_token)))

    @routes.post("/previews/{preview_id}/lease")
    async def lease(preview_id: str, request: Request) -> PreviewStatus:  # pyright: ignore[reportUnusedFunction]
        return await token_operation(request, preview_id, stop=False)

    @routes.post("/previews/{preview_id}/stop")
    async def stop(preview_id: str, request: Request) -> PreviewStatus:  # pyright: ignore[reportUnusedFunction]
        return await token_operation(request, preview_id, stop=True)

    router.routes.extend(routes.routes)
