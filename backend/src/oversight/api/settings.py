"""Live-only file settings routes, installed before the static frontend mount."""

from fastapi import FastAPI

from oversight.api.errors import ApiError
from oversight.settings import Settings
from oversight.settings_file import SettingsFile, SettingsRejected
from oversight_contracts.commands import SettingsView, UpdateSettings


def add_settings_routes(app: FastAPI, settings: Settings) -> None:
    file = SettingsFile(settings)

    @app.get("/settings")
    async def read_settings() -> SettingsView:  # pyright: ignore[reportUnusedFunction]
        try:
            return await file.read()
        except Exception:
            raise ApiError("invalid_request", "Cannot read or save the settings file.") from None

    @app.put("/settings")
    async def save_settings(request: UpdateSettings) -> SettingsView:  # pyright: ignore[reportUnusedFunction]
        try:
            return await file.save(request.values)
        except SettingsRejected as error:
            raise ApiError("invalid_request", str(error)) from None
        except Exception:
            raise ApiError("invalid_request", "Cannot read or save the settings file.") from None
