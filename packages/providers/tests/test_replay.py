"""Replay provider against real Postgres, alone and through the core worker."""
import threading
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from weave_core.adapters import AdapterContext, AdapterSpec, StageResult, UnitRef
from weave_core.config import RunConfig, StaticCatalog
from weave_core.db import make_engine
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import documents, jobs
from weave_providers.crawl.jobs import claim_crawl, create_crawl, finish_crawl, record_article
from weave_providers.crawl.requests import CrawlRequest
from weave_core.stages import Stage, UnitKind
from weave_core.worker import run_worker
from weave_providers.replay import ReplayProvider, ReplaySettings
from weave_providers.shape import content_hash


class Empty(BaseModel):
    pass


@pytest.fixture
def engine(database_url, monkeypatch):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def seed(engine, rows):
    """rows: (key, order_key, provider, language, outlet, published_at, url)"""
    recs = []
    for key, order_key, provider, lang, outlet, when, url in rows:
        text = f"text {key}"
        recs.append({
            "id": f"{provider}:{key}", "article_id": f"{provider}:{key}", "provider": provider, "external_id": key, "language": lang,
            "title": "", "byline": "", "outlet": outlet, "url": url, "published_at": when,
            "text": text, "token_offsets": [], "sentence_offsets": [],
            "content_hash": content_hash(text), "order_key": list(order_key)})
    with engine.begin() as c:
        c.execute(sa.insert(documents), recs)


def day(n):
    return datetime(2026, 11, n, tzinfo=timezone.utc)


def run_replay(**settings):
    provider = ReplayProvider(ReplaySettings(**settings), AdapterContext(uuid4(), 1, {}))
    return provider.process(UnitRef(UnitKind.run, "r", "h"), {})


def test_replay_emits_stored_documents_in_original_order(engine):
    seed(engine, [("a", (3,), "crawl", "en", "x", day(1), "u1"),
                  ("b", (1,), "crawl", "en", "x", day(2), "u2"),
                  ("c", (2,), "crawl", "en", "x", day(3), "u3")])
    result = run_replay()
    assert [u.order_key for u in result.emits] == [(1,), (2,), (3,)]
    assert [u.id for u in result.emits] == ["crawl:b", "crawl:c", "crawl:a"]
    assert all(u.kind == UnitKind.document and u.partition_hint is None for u in result.emits)
    assert result.emits[0].content_hash == content_hash("text b")
    assert result.output == {"documents": 3}


def test_replay_breaks_order_key_ties_by_url_then_id(engine):
    seed(engine, [("a", (1,), "crawl", "en", "x", None, "u2"),
                  ("b", (1,), "crawl", "en", "x", None, "u1"),
                  ("c", (1,), "crawl", "en", "x", None, "u1")])
    assert [u.id for u in run_replay().emits] == ["crawl:b", "crawl:c", "crawl:a"]


def test_replay_filters_and_writes_no_documents(engine):
    seed(engine, [("a", (1,), "crawl", "en", "x", day(1), "u1"),
                  ("b", (2,), "crawl", "es", "y", day(2), "u2"),
                  ("c", (3,), "crawl", "es", "z", day(3), "u3"),
                  ("d", (4,), "crawl", "es", "y", day(4), "u4"),
                  ("e", (5,), "mcecr", "es", "y", day(2), "u5")])
    assert run_replay(languages=["es"]).documents == []
    assert [u.id for u in run_replay(languages=["es"]).emits] == ["crawl:b", "crawl:c", "crawl:d"]
    assert [u.id for u in run_replay(outlets=["y"]).emits] == ["crawl:b", "crawl:d"]
    assert [u.id for u in run_replay(published_from=day(2), published_to=day(4)).emits] == [
        "crawl:b", "crawl:c"]
    assert [u.id for u in run_replay(limit=2).emits] == ["crawl:a", "crawl:b"]
    assert [u.id for u in run_replay(provider="mcecr").emits] == ["mcecr:e"]
    with engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(documents)).scalar() == 5


def test_replay_of_unknown_provider_is_empty_not_error(engine):
    seed(engine, [("a", (1,), "crawl", "en", "x", day(1), "u1")])
    result = run_replay(provider="nope")
    assert result.emits == [] and result.documents == [] and result.output == {"documents": 0}


def test_replay_exact_distinct_crawl_membership_and_validation(engine):
    seed(engine, [("a", (2,), "crawl", "en", "x", day(1), "u2"),
                  ("b", (1,), "crawl", "es", "x", day(2), "u1"),
                  ("c", (3,), "crawl", "en", "x", day(3), "u3")])
    request = CrawlRequest(sites=[{"domain": "example.com", "language": "en"}])
    first = create_crawl(engine, request)
    claim1 = claim_crawl(engine, datetime.now(timezone.utc))
    def member(claim, url, doc, status):
        assert record_article(engine, claim.id, claim.token, dict(
            canonical_url=url, url=url, outlet='x', status=status, document_id=doc))
    member(claim1, 'https://example.com/a', 'crawl:a', 'stored')
    member(claim1, 'https://example.com/b', 'crawl:b', 'stored')
    assert finish_crawl(engine, first, claim1.token)
    second = create_crawl(engine, request)
    claim2 = claim_crawl(engine, datetime.now(timezone.utc))
    member(claim2, 'https://example.com/a', 'crawl:a', 'existing')
    member(claim2, 'https://example.com/copy', 'crawl:a', 'duplicate')
    member(claim2, 'https://example.com/c', 'crawl:c', 'stored')
    with pytest.raises(ValueError, match='(?i)(not completed|running)'):
        run_replay(crawl_id=second)
    with pytest.raises(ValueError, match='(?i)(unknown|not found)'):
        run_replay(crawl_id=uuid4())
    assert finish_crawl(engine, second, claim2.token)
    assert [u.id for u in run_replay(crawl_id=first).emits] == ['crawl:b', 'crawl:a']
    assert [u.id for u in run_replay(crawl_id=second).emits] == ['crawl:a', 'crawl:c']
    assert [u.id for u in run_replay(crawl_id=first, languages=['en']).emits] == ['crawl:a']
    assert [u.id for u in run_replay(crawl_id=second, limit=1).emits] == ['crawl:a']


def test_second_identical_run_sees_documents_added_in_between(engine):
    seed(engine, [("a", (1,), "crawl", "en", "x", day(1), "u1")])
    registry = Registry()
    registry.register(ReplayProvider)
    cfg = RunConfig(name="t", track="realistic", seed=1, stages={
        Stage.provide: {"adapter": "replay"}, Stage.extract: {"adapter": "x"},
        Stage.enrich: {"adapter": "e"}, Stage.embed: {"adapter": "m"}, Stage.cluster: {"adapter": "c"}})
    for stage, name, kind in [(Stage.extract, "x", UnitKind.document), (Stage.enrich, "e", UnitKind.mention),
                              (Stage.embed, "m", UnitKind.mention), (Stage.cluster, "c", UnitKind.mention)]:
        class Fake:
            spec = AdapterSpec(name, "1", stage, kind, Empty)

            def __init__(self, settings, ctx):
                pass

            def process(self, unit, upstream):
                return StageResult({})
        registry.register(Fake)
    service = RunService(engine, registry, StaticCatalog())

    def provided(run):
        service.start(run)
        run_worker(engine, registry, Stage.provide, "w", stop=threading.Event(), once=True)
        with engine.connect() as c:
            return {r[0] for r in c.execute(sa.select(jobs.c.unit_id).where(
                jobs.c.run_id == run, jobs.c.stage == Stage.extract))}

    assert provided(service.create(cfg).run_id) == {"crawl:a"}
    seed(engine, [("b", (2,), "crawl", "en", "x", day(2), "u2")])
    assert provided(service.create(cfg).run_id) == {"crawl:a", "crawl:b"}


def test_paper_run_with_replay_fails_without_a_topic(engine):
    seed(engine, [("a", (1,), "crawl", "en", "x", day(1), "u1")])
    registry = Registry()
    registry.register(ReplayProvider)
    for stage, name, kind in [(Stage.extract, "x", UnitKind.document), (Stage.enrich, "e", UnitKind.mention),
                              (Stage.embed, "m", UnitKind.mention), (Stage.cluster, "c", UnitKind.mention)]:
        class Fake:
            spec = AdapterSpec(name, "1", stage, kind, Empty)

            def __init__(self, settings, ctx):
                pass

            def process(self, unit, upstream):
                return StageResult({})
        registry.register(Fake)
    cfg = RunConfig(name="t", track="paper", seed=1, stages={
        Stage.provide: {"adapter": "replay"}, Stage.extract: {"adapter": "x"},
        Stage.enrich: {"adapter": "e"}, Stage.embed: {"adapter": "m"}, Stage.cluster: {"adapter": "c"}})
    service = RunService(engine, registry, StaticCatalog())
    run = service.create(cfg).run_id
    service.start(run)
    run_worker(engine, registry, Stage.provide, "w", stop=threading.Event(), once=True)
    with engine.connect() as c:
        error = c.execute(sa.select(jobs.c.error).where(jobs.c.stage == Stage.provide)).scalar()
    assert error == "paper track needs a topic for document 'crawl:a'"
