"""The composition root: builds the FastAPI app and wires the modules together."""

from fastapi import FastAPI

from oversight.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Oversight")
    app.state.settings = settings or Settings.load()
    return app
