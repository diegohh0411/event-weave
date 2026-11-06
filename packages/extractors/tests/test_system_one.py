import pytest
import sqlalchemy as sa
from contract import run_contract
from ext_helpers import ctx, make_doc, unit
from pydantic import ValidationError
from stanza_fixtures import POLICE, POLICE_PARSE
from weave_core.schema import ledger
from weave_extractors.stanza_client import FixtureStanzaClient
from weave_extractors.system_one import SystemOneExtractor, SystemOneSettings
from weave_inference.clients.stub import set_stub_script

# Candidates of POLICE: c0 Police, c1 said, c2 protest, c3 broken, c4 attack


class S1(SystemOneExtractor):
    def __init__(self, doc, client, deployment="stub-s1", **settings):
        super().__init__(SystemOneSettings(**({"threshold": 0.5} | settings)), ctx(system_one=deployment))
        self._doc, self._client = doc, client

    def load_document(self, doc_id):
        return self._doc

    def client(self):
        return self._client


@pytest.fixture
def police(tmp_path):
    doc = make_doc(POLICE)
    client = FixtureStanzaClient(tmp_path)
    client.save(POLICE, "en", POLICE_PARSE)
    return doc, client


def test_threshold_picks_mentions(stub_inference, police):
    set_stub_script("stub", {"c0": 0.1, "c1": 0.9, "c2": 0.6, "c3": 0.2, "c4": 0.5})
    result = S1(*police).process(unit(police[0]), {})
    assert [(m["text"], m["probability"]) for m in result.output["mentions"]] == [
        ("said", 0.9), ("protest", 0.6), ("attack", 0.5)]
    assert result.output["candidates"] == [[0, 6, 0.1], [7, 11, 0.9], [16, 23, 0.6], [28, 34, 0.2], [48, 54, 0.5]]


def test_splits_on_limit(stub_inference, police, db):
    doc, client = police
    set_stub_script("stub", {f"c{i}": 0.9 for i in range(5)})
    probe = S1(doc, client)
    two = probe.build_request(doc, POLICE_PARSE, probe_items(probe, doc)[:2])
    stub_inference(context_tokens=len(two.text_for_context.encode()) // 4)
    result = S1(doc, client, deployment="stub-s1-small").process(unit(doc), {})
    assert len(result.output["candidates"]) == 5 and all(p == 0.9 for *_, p in result.output["candidates"])
    with db.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(ledger).where(ledger.c.status == "ok")) >= 3


def probe_items(extractor, doc):
    from weave_extractors.candidates import candidates
    return list(enumerate(candidates(doc, POLICE_PARSE)[0]))


def test_threshold_required():
    with pytest.raises(ValidationError):
        SystemOneSettings()
    with pytest.raises(ValidationError):
        SystemOneSettings(threshold=1.5)


def test_system_one_contract(stub_inference, police):
    set_stub_script("stub", {f"c{i}": 0.7 for i in range(5)})
    run_contract(lambda d: S1(d, police[1]), [police[0]])


def test_mention_identity_depends_on_deployment(stub_inference, police):
    set_stub_script("stub", {f"c{i}": 0.9 for i in range(5)})
    a = S1(*police, deployment="stub-s1").process(unit(police[0]), {})
    b = S1(*police, deployment="stub-s1-small").process(unit(police[0]), {})
    assert {e.id for e in a.emits}.isdisjoint({e.id for e in b.emits})
    assert {r["deployment"] for r in a.rows["mentions"]} == {"stub-s1"}
