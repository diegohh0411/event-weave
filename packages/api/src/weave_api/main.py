from contextlib import asynccontextmanager

from fastapi import FastAPI

from weave_core.db import migrate
from weave_core.settings import Settings


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.schema = migrate(Settings.from_env().database_url)
        yield

    app = FastAPI(title="Event Weave", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema": app.state.schema}

    return app
