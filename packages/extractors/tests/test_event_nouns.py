import sqlalchemy as sa
from ext_helpers import make_doc, store
from stanza_fixtures import POLICE, POLICE_PARSE
from typer.testing import CliRunner
from weave_core.schema import gold_documents, gold_mentions
from weave_extractors.event_nouns import build_event_nouns
from weave_extractors.stanza_client import FixtureStanzaClient


def seed(engine, doc, split, spans):
    store(engine, doc)
    with engine.begin() as c:
        c.execute(sa.insert(gold_documents).values(document_id=doc.id, language="english", topic="t",
                                                   split=split, doc_index=0))
        c.execute(sa.insert(gold_mentions), [{"document_id": doc.id, "mention_id": f"T{i}", "start": s, "end": e,
                                              "text": doc.text[s:e]} for i, (s, e) in enumerate(spans)])


def test_nouns_come_from_train_gold_nouns_only(db, tmp_path):
    train, dev = make_doc(POLICE, doc_id="fake:train"), make_doc(POLICE, doc_id="fake:dev")
    seed(db, train, "train", [(7, 11), (48, 54)])       # said (verb), attack (noun)
    seed(db, dev, "dev", [(16, 23)])                    # protest: dev only, never listed
    client = FixtureStanzaClient(tmp_path)
    client.save(POLICE, "en", POLICE_PARSE)
    assert build_event_nouns(db, client) == {"en": ["attack"]}


def test_cli_writes_one_file_per_language(db, tmp_path, monkeypatch):
    from weave_extractors import cli
    seed(db, make_doc(POLICE, doc_id="fake:train"), "train", [(48, 54)])
    client = FixtureStanzaClient(tmp_path / "fx")
    client.save(POLICE, "en", POLICE_PARSE)
    monkeypatch.setattr(cli, "HttpStanzaClient", lambda url: client)
    result = CliRunner().invoke(cli.app, ["event-nouns", "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "en.txt").read_text() == "attack\n"
    assert "en: 1" in result.output
