from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import NoResultFound

from weave_core.config import ConfigError
from weave_core.db import migrate
from weave_core.runs import RunService, RunStateError
from weave_core.service import build_service

from .routers import adapters, clusters, deployments, documents, enrichments, runs, scores

# The web app is served from these origins and calls the API from the browser.
WEB_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]


def create_app(service: RunService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = service is None
        app.state.service = build_service(probe=True) if owned else service
        app.state.schema = migrate(app.state.service.engine.url.render_as_string(hide_password=False))
        try:
            yield
        finally:
            if owned:
                app.state.service.engine.dispose()

    app = FastAPI(title="Event Weave", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=WEB_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError):
        return JSONResponse({"problems": exc.problems}, status_code=422)

    @app.exception_handler(RunStateError)
    async def run_state_error(request: Request, exc: RunStateError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NoResultFound)
    async def not_found(request: Request, exc: NoResultFound):
        return JSONResponse({"detail": "not found"}, status_code=404)

    app.include_router(adapters.router)
    app.include_router(deployments.router)
    app.include_router(runs.router)
    app.include_router(clusters.router)
    app.include_router(documents.router)
    app.include_router(enrichments.router)
    app.include_router(scores.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema": app.state.schema}

    return app
