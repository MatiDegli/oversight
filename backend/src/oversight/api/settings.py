"""Live-only file settings routes, installed before the static frontend mount."""

from fastapi import FastAPI

from oversight.api.errors import ApiError
from oversight.settings import Settings
from oversight.settings_file import SettingsFile
from oversight_contracts.commands import SettingsView, UpdateSettings


def add_settings_routes(app: FastAPI, settings: Settings) -> None:
    file = SettingsFile(settings)

    @app.get("/settings")
    async def read_settings() -> SettingsView:  # pyright: ignore[reportUnusedFunction]
        try:
            return await file.read()
        except (ValueError, OSError):
            raise ApiError("invalid_request", "Cannot read settings file.") from None

    @app.put("/settings")
    async def save_settings(request: UpdateSettings) -> SettingsView:  # pyright: ignore[reportUnusedFunction]
        try:
            return await file.save(request.values)
        except ValueError as error:
            raise ApiError("invalid_request", str(error)) from None
        except OSError:
            raise ApiError("invalid_request", "Cannot save settings file.") from None
