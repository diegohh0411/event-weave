from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import cluster_decisions, cluster_members, clusters, documents, jobs, mentions, runs, stage_outputs

TEXTS = {"en": "Rebels seized the town. Troops retook it on Monday.", "es": "Los rebeldes tomaron el pueblo. Las tropas lo recuperaron."}


def _doc(conn, did, language):
    text = TEXTS[language]
    first = text.index(".") + 1
    conn.execute(documents.insert().values(
        id=did, article_id=did, provider="p", external_id=did, language=language, title=f"title {did}", byline="", outlet="", url="",
        text=text, token_offsets=[], sentence_offsets=[[0, first], [first + 1, len(text)]], content_hash="h"))


def _mention(conn, mid, did, language, sentence, word):
    text = TEXTS[language]
    s0 = 0 if sentence == 0 else text.index(".") + 2
    start = text.index(word, s0)
    conn.execute(mentions.insert().values(id=mid, document_id=did, start=start, end=start + len(word), tok_start=0, tok_end=1,
                                          sentence=sentence, text=word, extractor="x", extractor_version="1"))


def _cluster(conn, run, cid, size, order, card, members):
    conn.execute(clusters.insert().values(id=cid, run_id=run, partition="pt", card=card, card_hash=f"hash-{cid}", size=size, created_order=[order]))
    for i, mid in enumerate(members):
        conn.execute(cluster_members.insert().values(run_id=run, mention_id=mid, cluster_id=cid, partition="pt", order_key=[order, i]))
        conn.execute(cluster_decisions.insert().values(
            run_id=run, mention_id=mid, adapter="a", adapter_version="1", deployment="dep",
            candidates=[{"cluster_id": "other", "score": 0.5, "field_scores": {"action": 0.4}, "card_hash": "c", "shown_card_hash": "s", "label": "A"}],
            noul=0.1, probabilities={"A": 0.9}, outcome="join" if i else "new", reason="r", cluster_id=cid))


def _tag(conn, run, mid, significance, event_type):
    key = f"tag-{run}-{mid}"
    conn.execute(stage_outputs.insert().values(
        cache_key=key, stage="tag", adapter="t", adapter_version="1", emits={}, created_at=datetime.now(timezone.utc),
        output={"significance": significance, "event_type": event_type, "event_type_probs": None}))
    conn.execute(jobs.insert().values(run_id=run, stage="tag", unit_kind="mention", unit_id=mid, unit_content_hash="u", partition="p",
                                      order_key=[0], input_hash="i", status="done", available_at=datetime.now(timezone.utc), cache_key=key))


@pytest.fixture
def seeded(engine):
    run, other = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for r in (run, other):
            conn.execute(runs.insert().values(id=r, config={}, config_hash="h", state="done", created_at=now))
        _doc(conn, "d-en", "en")
        _doc(conn, "d-es", "es")
        _mention(conn, "m1", "d-en", "en", 0, "Rebels")
        _mention(conn, "m2", "d-en", "en", 1, "Troops")
        _mention(conn, "m3", "d-es", "es", 0, "rebeldes")
        _mention(conn, "m4", "d-es", "es", 1, "tropas")
        for r, mid, sig, kind in [(run, "m1", 0.9, "attack"), (run, "m2", 0.2, "military"), (run, "m3", 0.5, "attack"),
                                  (run, "m4", 0.1, "other"), (other, "m1", 0.99, "diplomacy")]:
            _tag(conn, r, mid, sig, kind)
        _cluster(conn, run, "c-big", 2, 5, {"action": "seize town"}, ["m1", "m3"])
        _cluster(conn, run, "c-mid", 1, 9, {"action": "retake"}, ["m2"])
        _cluster(conn, run, "c-es", 1, 1, {"action": "recuperar"}, ["m4"])
        _cluster(conn, other, "o-1", 4, 0, {"action": "seize town"}, ["m1"])
    return run


@pytest.fixture
def api(service):
    with TestClient(create_app(service)) as c:
        yield c


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [c["id"] for c in resp.json()["items"]]


def test_search_is_run_scoped(api, seeded):
    body = api.get(f"/runs/{seeded}/clusters").json()
    assert ids(api.get(f"/runs/{seeded}/clusters")) == ["c-big", "c-es", "c-mid"]
    assert body["total"] == 3
    assert api.get(f"/runs/{seeded}/clusters/o-1").status_code == 404
    assert api.get(f"/runs/{uuid4()}/clusters").status_code == 404


def test_filters_language_event_type_significance(api, seeded):
    base = f"/runs/{seeded}/clusters"
    assert ids(api.get(base, params={"language": "es"})) == ["c-big", "c-es"]
    assert ids(api.get(base, params={"event_type": "military"})) == ["c-mid"]
    assert ids(api.get(base, params={"min_significance": 0.6})) == ["c-big"]
    assert ids(api.get(base, params={"min_size": 2})) == ["c-big"]
    assert ids(api.get(base, params={"max_size": 1})) == ["c-es", "c-mid"]
    assert ids(api.get(base, params={"q": "REBELDES"})) == ["c-big"]
    assert ids(api.get(base, params={"q": "retake"})) == ["c-mid"]
    assert ids(api.get(base, params={"q": "%"})) == []
    summary = api.get(base, params={"min_size": 2}).json()["items"][0]
    assert summary == {"id": "c-big", "partition": "pt", "size": 2, "card": {"action": "seize town"}, "languages": ["en", "es"],
                       "event_types": ["attack"], "max_significance": 0.9}


def test_sort_size_and_recent(api, seeded):
    base = f"/runs/{seeded}/clusters"
    assert ids(api.get(base, params={"sort": "size"})) == ["c-big", "c-es", "c-mid"]
    assert ids(api.get(base, params={"sort": "recent"})) == ["c-mid", "c-big", "c-es"]
    assert api.get(base, params={"sort": "bogus"}).status_code == 422


def test_detail_and_unknown(api, seeded):
    body = api.get(f"/runs/{seeded}/clusters/c-big").json()
    assert body["card_hash"] == "hash-c-big" and body["cluster"]["size"] == 2
    assert api.get(f"/runs/{seeded}/clusters/nope").status_code == 404
    assert api.get(f"/runs/{seeded}/clusters/nope/members").status_code == 404


def test_every_member_has_its_decision(api, seeded):
    items = api.get(f"/runs/{seeded}/clusters/c-big/members").json()["items"]
    assert [m["mention_id"] for m in items] == ["m1", "m3"]
    for m in items:
        d = m["decision"]
        assert d["adapter"] == "a" and d["deployment"] == "dep" and d["noul"] == 0.1 and d["probabilities"] == {"A": 0.9}
        assert d["candidates"] == [{"cluster_id": "other", "label": "A", "score": 0.5, "field_scores": {"action": 0.4}}]
    assert items[0]["decision"]["outcome"] == "new" and items[1]["decision"]["outcome"] == "join"
    assert items[0]["document_title"] == "title d-en" and items[1]["language"] == "es"
    assert items[0]["significance"] == 0.9 and items[0]["event_type"] == "attack"


def test_trigger_offsets_slice_sentence_to_mention_text(api, seeded):
    for cid in ("c-big", "c-mid", "c-es"):
        for m in api.get(f"/runs/{seeded}/clusters/{cid}/members").json()["items"]:
            a, b = m["trigger"]
            assert m["sentence"][a:b] == m["text"]
    m = api.get(f"/runs/{seeded}/clusters/c-mid/members").json()["items"][0]
    assert m["sentence"] == "Troops retook it on Monday."


def test_members_paged(api, engine):
    run = uuid4()
    with engine.begin() as conn:
        conn.execute(runs.insert().values(id=run, config={}, config_hash="h", state="done", created_at=datetime.now(timezone.utc)))
        _doc(conn, "d", "en")
        for i in range(250):
            _mention(conn, f"mm{i}", "d", "en", 0, "Rebels")
        _cluster(conn, run, "big", 250, 0, {}, [f"mm{i}" for i in range(250)])
    page = api.get(f"/runs/{run}/clusters/big/members", params={"limit": 200}).json()
    assert len(page["items"]) == 200 and page["total"] == 250
    tail = api.get(f"/runs/{run}/clusters/big/members", params={"limit": 200, "offset": 200}).json()
    assert len(tail["items"]) == 50
    assert api.get(f"/runs/{run}/clusters/big/members", params={"limit": 500}).status_code == 422


def test_members_without_sentence_offsets_fall_back_to_document(api, engine):
    run = uuid4()
    with engine.begin() as conn:
        conn.execute(runs.insert().values(id=run, config={}, config_hash="h", state="done", created_at=datetime.now(timezone.utc)))
        _doc(conn, "d", "en")
        conn.execute(documents.update().where(documents.c.id == "d").values(sentence_offsets=[]))
        _mention(conn, "a", "d", "en", 1, "Troops")
        _mention(conn, "b", "d", "en", 0, "Rebels")
        conn.execute(mentions.update().where(mentions.c.id == "b").values(sentence=7))
        _cluster(conn, run, "c", 2, 0, {}, ["a", "b"])
    items = api.get(f"/runs/{run}/clusters/c/members").json()["items"]
    assert len(items) == 2
    for m in items:
        assert m["sentence"] == TEXTS["en"]
        a, b = m["trigger"]
        assert m["sentence"][a:b] == m["text"]


def test_tags_come_only_from_the_requested_runs_tag_job(api, seeded):
    # m1 is tagged attack/0.9 in the seeded run and diplomacy/0.99 in the other run
    big = api.get(f"/runs/{seeded}/clusters/c-big").json()["cluster"]
    assert big["event_types"] == ["attack"] and big["max_significance"] == 0.9
    first = api.get(f"/runs/{seeded}/clusters/c-big/members").json()["items"][0]
    assert first["event_type"] == "attack" and first["significance"] == 0.9
