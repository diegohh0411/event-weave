from fastapi import Request
from sqlalchemy import Engine

from weave_core.runs import RunService


def get_service(request: Request) -> RunService:
    return request.app.state.service


def get_engine(request: Request) -> Engine:
    return request.app.state.service.engine
