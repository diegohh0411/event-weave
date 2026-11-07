from collections.abc import Callable
from typing import Annotated, Generic, TypeVar

import sqlalchemy as sa
from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Row, Select

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int


class Paging:
    def __init__(self, offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        self.offset, self.limit = offset, limit


def paginate(conn, query: Select, paging: Paging, row: Callable[[Row], T]) -> Page[T]:
    total = conn.execute(sa.select(sa.func.count()).select_from(query.order_by(None).subquery())).scalar_one()
    rows = conn.execute(query.offset(paging.offset).limit(paging.limit)).all()
    return Page(items=[row(r) for r in rows], total=total, offset=paging.offset, limit=paging.limit)
