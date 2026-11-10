from datetime import datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import Query
from sqlalchemy import Select

from weave_core import library as status
from weave_core.schema import crawled_pages, documents, page_rechecks

Change = Literal['edited', 'removed', 'unchanged']


class LibraryFilter:
    def __init__(self, outlets: Annotated[list[str] | None, Query()] = None,
                 languages: Annotated[list[str] | None, Query()] = None,
                 providers: Annotated[list[str] | None, Query()] = None,
                 published_from: datetime | None = None, published_to: datetime | None = None,
                 search: str | None = None, change: Annotated[list[Change] | None, Query()] = None,
                 exclude_edited: bool = False, exclude_removed: bool = False):
        self.outlets, self.languages, self.providers = outlets, languages, providers
        self.published_from, self.published_to, self.search = published_from, published_to, search
        self.change, self.exclude_edited, self.exclude_removed = change, exclude_edited, exclude_removed


def library_query(f: LibraryFilter) -> Select:
    query = sa.select(documents).where(documents.c.superseded_at.is_(None))
    if f.providers is not None:
        query = query.where(documents.c.provider.in_(f.providers))
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
    if f.exclude_edited:
        query = query.where(~status.edited())
    if f.exclude_removed:
        query = query.where(~status.removed())
    if f.change:
        clauses = {'edited': status.edited(), 'removed': status.removed(),
                   'unchanged': sa.and_(~status.edited(), ~status.removed())}
        query = query.where(sa.or_(*(clauses[c] for c in f.change)))
    return query


def _extras():
    versions = documents.alias('versions')
    count = sa.select(sa.func.count()).select_from(versions).where(
        versions.c.article_id == documents.c.article_id).scalar_subquery()
    return [count.label('versions'),
            status.page_value(crawled_pages.c.gone_at).label('removed_at'),
            status.page_value(crawled_pages.c.gone_reason).label('removed_reason'),
            status.last_checked().label('last_checked_at'),
            status.page_value(crawled_pages.c.recheck_due_at, aggregate=sa.func.min).label('next_check_at')]


def listing(f: LibraryFilter) -> Select:
    return library_query(f).add_columns(*_extras()).order_by(documents.c.order_key, documents.c.id)


def document_row(conn, document_id: str):
    return conn.execute(sa.select(documents, *_extras()).where(documents.c.id == document_id)).one()


def rechecks(conn, article_id: str):
    versions = sa.select(documents.c.id).where(documents.c.article_id == article_id)
    return conn.execute(sa.select(page_rechecks).join(crawled_pages, crawled_pages.c.canonical_url == page_rechecks.c.canonical_url)
                        .where(crawled_pages.c.document_id.in_(versions))
                        .order_by(page_rechecks.c.checked_at.desc(), page_rechecks.c.id.desc())).all()


def history(conn, article_id: str):
    return conn.execute(sa.select(documents.c.id, documents.c.version, documents.c.superseded_at)
                        .where(documents.c.article_id == article_id).order_by(documents.c.version)).all()


def facets(conn, f: LibraryFilter):
    filtered = library_query(f).subquery()
    total = conn.scalar(sa.select(sa.func.count()).select_from(filtered))
    def counts(column):
        return conn.execute(sa.select(column.label('value'), sa.func.count().label('count'))
                            .group_by(column).order_by(column)).all()
    edited = conn.scalar(sa.select(sa.func.count()).select_from(filtered).where(status.edited(filtered.c)))
    removed = conn.scalar(sa.select(sa.func.count()).select_from(filtered).where(status.removed(filtered.c)))
    changes = [(value, n) for value, n in (('edited', edited), ('removed', removed)) if n]
    return total, counts(filtered.c.outlet), counts(filtered.c.language), changes
