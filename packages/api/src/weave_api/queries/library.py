from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import Query
from sqlalchemy import Select

from weave_core.schema import documents


class LibraryFilter:
    def __init__(self, outlets: Annotated[list[str] | None, Query()] = None,
                 languages: Annotated[list[str] | None, Query()] = None,
                 published_from: datetime | None = None, published_to: datetime | None = None,
                 search: str | None = None):
        self.outlets, self.languages = outlets, languages
        self.published_from, self.published_to, self.search = published_from, published_to, search


def library_query(f: LibraryFilter) -> Select:
    query = sa.select(documents).where(documents.c.superseded_at.is_(None))
    if f.outlets is not None:
        query = query.where(documents.c.outlet.in_(f.outlets))
    if f.languages is not None:
        query = query.where(documents.c.language.in_(f.languages))
    if f.published_from is not None:
        query = query.where(documents.c.published_at >= f.published_from)
    if f.published_to is not None:
        query = query.where(documents.c.published_at < f.published_to)
    if f.search:
        query = query.where(documents.c.title.icontains(f.search, autoescape=True))
    return query


def listing(f: LibraryFilter) -> Select:
    versions = documents.alias('versions')
    count = sa.select(sa.func.count()).select_from(versions).where(
        versions.c.article_id == documents.c.article_id).scalar_subquery()
    return library_query(f).add_columns(count.label('versions')).order_by(documents.c.order_key, documents.c.id)


def document_row(conn, document_id: str):
    return conn.execute(sa.select(documents).where(documents.c.id == document_id)).one()


def history(conn, article_id: str):
    return conn.execute(sa.select(documents.c.id, documents.c.version, documents.c.superseded_at)
                        .where(documents.c.article_id == article_id).order_by(documents.c.version)).all()


def facets(conn, f: LibraryFilter):
    filtered = library_query(f).subquery()
    total = conn.scalar(sa.select(sa.func.count()).select_from(filtered))
    def counts(column):
        return conn.execute(sa.select(column.label('value'), sa.func.count().label('count'))
                            .group_by(column).order_by(column)).all()
    return total, counts(filtered.c.outlet), counts(filtered.c.language)
