from fastapi import Request
from sqlalchemy import Engine

from weave_core.runs import RunService
from weave_eval.paper import PaperNumbers, load_paper


def get_service(request: Request) -> RunService:
    return request.app.state.service


def get_engine(request: Request) -> Engine:
    return request.app.state.service.engine


def get_paper() -> PaperNumbers:
    """The published MCECR numbers; tests override this dependency with a temporary file."""
    return load_paper()
