"""Change status of library documents, shared by the library API and run selection."""
import sqlalchemy as sa

from weave_core.schema import crawled_pages, documents, page_rechecks


def edited(d=documents.c):
    """The article has more than one stored version."""
    versions = documents.alias("edit_versions")
    return sa.exists().where(versions.c.article_id == d.article_id, versions.c.id != d.id)


def removed(d=documents.c):
    """A recheck found the page gone (404/410) or redirected away."""
    return sa.exists().where(crawled_pages.c.document_id == d.id, crawled_pages.c.status == "gone")


def page_value(column, d=documents.c, aggregate=sa.func.max):
    return sa.select(aggregate(column)).where(crawled_pages.c.document_id == d.id).scalar_subquery()


def last_checked(d=documents.c):
    return (sa.select(sa.func.max(page_rechecks.c.checked_at))
            .join(crawled_pages, crawled_pages.c.canonical_url == page_rechecks.c.canonical_url)
            .where(crawled_pages.c.document_id == d.id).scalar_subquery())
