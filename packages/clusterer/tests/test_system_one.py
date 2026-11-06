import json
import re

import pytest
import sqlalchemy as sa
from weave_clusterer.decisions import cached_system_one
from weave_clusterer.system_one import SystemOneClusterer, build_request
from weave_core.schema import cluster_decisions, jobs, ledger
from weave_core.stages import Capability
from weave_core.adapters import AdapterContext
from weave_inference.catalog import Catalog
from weave_inference.clients import stub
from weave_inference.clients.stub import clear_stub_scripts, set_stub_script
from weave_inference.gateway import Inference
from weave_inference.limits import check_limits
from weave_inference.probe import Readiness
from weave_inference.types import Choice, Deployment, Limits, Model, Noul, Provider, SystemOneRequest

from helpers import decisions, drain, make_pipeline_run, membership, seed_unit, unit

SETTINGS = {"k": 5, "weights": {"action": 1.0, "participants": 1.0, "location": 1.0, "time": 1.0}, "threshold": 0.9}
SEEN: list = []


@pytest.fixture
def s1(db, monkeypatch):
    """Stub inference with embeddings and System One deployments; make(context_tokens=...) reconfigures."""
    SEEN.clear()
    original = stub.StubClient.call

    def spy(self, capability, request):
        if capability == Capability.system_one:
            SEEN.append(request)
        return original(self, capability, request)

    monkeypatch.setattr(stub.StubClient, "call", spy)

    def make(context_tokens=None):
        catalog = Catalog(
            models={"emb": Model(id="emb", capability=Capability.embeddings),
                    "m-s1": Model(id="m-s1", capability=Capability.system_one)},
            providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
            deployments={"stub-emb": Deployment(id="stub-emb", model="emb", provider="stub"),
                         "stub-s1": Deployment(id="stub-s1", model="m-s1", provider="stub",
                                               limits=Limits(context_tokens=context_tokens)),
                         "stub-s1-3": Deployment(id="stub-s1-3", model="m-s1", provider="stub",
                                                 limits=Limits(max_choice_options=3))})
        catalog.readiness = {k: Readiness(ready=True) for k in catalog.deployments}
        Inference.configure(catalog, db)
        return catalog

    make()
    yield make
    clear_stub_scripts()


def new_run(db, *, deployment="stub-s1", partition="part-x", settings=None, n=0, start=0.0):
    """A run with n seeded, mutually distant mentions m1..mn (none yet clustered)."""
    with db.begin() as c:
        run = make_pipeline_run(c, settings or SETTINGS, "system-one", {"system_one": deployment})
        for i in range(1, n + 1):
            seed_unit(c, run, f"m{i}", (i,), {"action": unit(start + i * 0.5)}, partition=partition)
    return run


def add_mention(db, run, mid, order, angle, partition="part-x", **kw):
    with db.begin() as c:
        seed_unit(c, run, mid, (order,), {"action": unit(angle)}, partition=partition, **kw)
        c.execute(sa.text("UPDATE runs SET state = 'running' WHERE id = :r"), {"r": run})


def ledger_rows(db, run, status=None):
    q = sa.select(sa.func.count()).select_from(ledger).where(ledger.c.run_id == run, ledger.c.stage == "cluster")
    if status:
        q = q.where(ledger.c.status == status)
    with db.connect() as c:
        return c.scalar(q)


def build_clusters(db, run, n):
    """n singleton clusters: default stub answers Noul 0, so every mention opens its own."""
    drain(db, SystemOneClusterer)
    assert len(set(membership(db, run).values())) == n


def test_request_has_no_ids_or_gold(db, s1):
    set_stub_script("stub", {"same": 0.95})
    run = new_run(db, n=6, partition="part-zq9")
    drain(db, SystemOneClusterer)
    assert SEEN
    text = "\n".join(json.dumps(r.model_dump(mode="json")) for r in SEEN)
    got = membership(db, run)
    with db.connect() as c:
        doc_ids = [r[0] for r in c.execute(sa.text("select id from documents"))]
        topic_like = ["topic", "gold"]
    forbidden = set(got.values()) | set(doc_ids) | {"part-zq9", *topic_like}
    for needle in forbidden:
        assert needle not in text, needle
    assert not re.search(r"\b(train|dev|test)\b", text)


def test_choice_options_never_exceed_limit(db, s1):
    run = new_run(db, deployment="stub-s1-3", settings={**SETTINGS, "k": 10}, n=6)
    build_clusters(db, run, 6)
    SEEN.clear()
    set_stub_script("stub", {"same": 0.95})
    add_mention(db, run, "m7", 7, 0.5)
    drain(db, SystemOneClusterer)
    choices = [q for r in SEEN for q in r.questions if isinstance(q, Choice)]
    assert choices and all(len(q.options) <= 3 for q in choices) and max(len(q.options) for q in choices) == 3
    assert len(decisions(db, run)["m7"].candidates) == 3


def test_cards_fit_context(db, s1):
    run = new_run(db, n=5)
    drain(db, SystemOneClusterer)
    SEEN.clear()
    catalog = s1(context_tokens=110)
    set_stub_script("stub", {"same": 0.95})
    add_mention(db, run, "m6", 6, 0.5)
    drain(db, SystemOneClusterer)
    assert SEEN
    for r in SEEN:
        check_limits(catalog.deployments["stub-s1"], r)
    d = decisions(db, run)["m6"]
    assert d.reason.endswith("+trimmed") and d.outcome == "join"
    assert ledger_rows(db, run, "refused") == 0
    offered = [c for c in d.candidates if c["label"]]
    assert 1 <= len(offered) < len(d.candidates)


def test_roomy_context_is_not_trimmed(db, s1):
    run = new_run(db, n=3)
    drain(db, SystemOneClusterer)
    SEEN.clear()
    s1(context_tokens=10_000)
    set_stub_script("stub", {"same": 0.95})
    add_mention(db, run, "m4", 4, 0.5)
    drain(db, SystemOneClusterer)
    assert not decisions(db, run)["m4"].reason.endswith("+trimmed")


def test_invalid_choice_is_new(db, s1):
    run = new_run(db, n=3)
    build_clusters(db, run, 3)
    set_stub_script("stub", {"same": 0.95, "which": {"question_id": "which", "value": "c9",
                                                      "probabilities": {"c1": 0.1, "c2": 0.1, "c3": 0.1}}})
    add_mention(db, run, "m4", 4, 0.5)
    drain(db, SystemOneClusterer)
    got, d = membership(db, run), decisions(db, run)["m4"]
    assert (d.outcome, d.reason, d.noul) == ("new", "invalid_choice", 0.95)
    assert got["m4"] not in {got["m1"], got["m2"], got["m3"]}


def test_choice_missing_is_invalid(db, s1):
    run = new_run(db, n=2)
    build_clusters(db, run, 2)
    set_stub_script("stub", {"same": 0.95, "which": {"question_id": "which", "value": None}})
    add_mention(db, run, "m3", 3, 0.5)
    drain(db, SystemOneClusterer)
    assert decisions(db, run)["m3"].reason == "invalid_choice"


def test_noul_below_threshold_is_new(db, s1):
    run = new_run(db, n=2)
    build_clusters(db, run, 2)
    set_stub_script("stub", {"same": 0.2, "which": "c1"})
    add_mention(db, run, "m3", 3, 0.5)
    drain(db, SystemOneClusterer)
    d = decisions(db, run)["m3"]
    assert (d.outcome, d.reason, d.noul) == ("new", "noul_below_threshold", 0.2)


def test_choice_joins_the_labelled_candidate(db, s1):
    run = new_run(db, n=3)
    build_clusters(db, run, 3)
    set_stub_script("stub", {"same": 0.95, "which": {"question_id": "which", "value": "c2",
                                                      "probabilities": {"c1": 0.1, "c2": 0.8, "c3": 0.1}}})
    add_mention(db, run, "m4", 4, 0.5)
    drain(db, SystemOneClusterer)
    d = decisions(db, run)["m4"]
    second = d.candidates[1]["cluster_id"]
    assert (d.outcome, d.cluster_id) == ("join", second)
    assert d.probabilities == {"c1": 0.1, "c2": 0.8, "c3": 0.1}


def test_single_candidate_no_choice(db, s1):
    run = new_run(db, n=1)
    build_clusters(db, run, 1)
    SEEN.clear()
    set_stub_script("stub", {"same": 0.95})
    add_mention(db, run, "m2", 2, 0.5)
    drain(db, SystemOneClusterer)
    assert len(SEEN) == 1 and [type(q) for q in SEEN[0].questions] == [Noul]
    got, d = membership(db, run), decisions(db, run)["m2"]
    assert got["m2"] == got["m1"] and d.outcome == "join" and d.probabilities is None


def test_fewer_clusters_than_k_offers_only_existing(db, s1):
    run = new_run(db, settings={**SETTINGS, "k": 8}, n=3)
    build_clusters(db, run, 3)
    SEEN.clear()
    set_stub_script("stub", {"same": 0.95})
    add_mention(db, run, "m4", 4, 0.5)
    drain(db, SystemOneClusterer)
    (choice,) = [q for q in SEEN[0].questions if isinstance(q, Choice)]
    assert choice.options == ["c1", "c2", "c3"]


def test_rerun_identical_no_new_calls(db, s1):
    set_stub_script("stub", {"same": 0.95})
    run1 = new_run(db, n=6)
    drain(db, SystemOneClusterer)
    first = membership(db, run1)
    assert ledger_rows(db, run1) > 0 and len(set(first.values())) < 6
    with db.begin() as c:
        run2 = make_pipeline_run(c, SETTINGS, "system-one", {"system_one": "stub-s1"})
        ids = {}
        for r in c.execute(sa.select(jobs).where(jobs.c.run_id == run1).order_by(jobs.c.id)).mappings():
            ids[r["id"]] = c.execute(sa.insert(jobs).values(
                run_id=run2, stage=r["stage"], unit_kind=r["unit_kind"], unit_id=r["unit_id"],
                unit_content_hash=r["unit_content_hash"], parent_job_id=ids.get(r["parent_job_id"]),
                partition=r["partition"], order_key=r["order_key"], input_hash=r["input_hash"],
                status="pending" if r["stage"] == "cluster" else "done", attempts=0,
                available_at=r["available_at"], cache_key=r["cache_key"]).returning(jobs.c.id)).scalar_one()
    drain(db, SystemOneClusterer)
    second = membership(db, run2)

    def partition_of(m):
        groups = {}
        for mention, cluster in m.items():
            groups.setdefault(cluster, set()).add(mention)
        return {frozenset(v) for v in groups.values()}

    assert partition_of(second) == partition_of(first) and len(second) == 6
    assert ledger_rows(db, run2) == 0


def test_decision_record_complete(db, s1):
    run = new_run(db, n=3)
    build_clusters(db, run, 3)
    set_stub_script("stub", {"same": 0.95, "which": {"question_id": "which", "value": "c1",
                                                      "probabilities": {"c1": 0.7, "c2": 0.2, "c3": 0.1}}})
    add_mention(db, run, "m4", 4, 0.5)
    drain(db, SystemOneClusterer)
    d = decisions(db, run)["m4"]
    assert (d.adapter, d.deployment, d.noul, d.outcome) == ("system-one", "stub-s1", 0.95, "join")
    assert [c["label"] for c in d.candidates] == ["c1", "c2", "c3"]
    assert all(isinstance(c["score"], float) and re.fullmatch(r"[0-9a-f]{64}", c["card_hash"]) for c in d.candidates)
    assert d.probabilities == {"c1": 0.7, "c2": 0.2, "c3": 0.1}
    assert d.candidates[0]["cluster_id"] == d.cluster_id


def test_cache_hit_makes_no_call_and_no_ledger_row(db, s1):
    run = new_run(db, n=0)
    ctx = AdapterContext(run, 1, {Capability.system_one: "stub-s1"}, None, "system-one")
    inference = Inference.for_context(ctx)
    req = SystemOneRequest(state="s", questions=[Noul(id="same", prompt="p")])
    from sqlalchemy.orm import Session
    with Session(db) as session:
        a = cached_system_one(inference, session, req)
        session.commit()
        n = len(SEEN)
        b = cached_system_one(inference, session, req)
    assert a == b and len(SEEN) == n == 1
