from fastapi.testclient import TestClient
from stanza_fixtures import POLICE, POLICE_PARSE
from weave_extractors.stanza_client import HttpStanzaClient
from weave_extractors.stanza_service import create_app


def fake_pipeline(lang):
    assert lang == "en"
    return lambda text: POLICE_PARSE


def test_service_parse_shape(tmp_path):
    client = TestClient(create_app(tmp_path, pipeline_factory=fake_pipeline))
    body = client.post("/parse", json={"text": POLICE, "lang": "en"}).json()
    assert body["sentences"][0]["words"][1] == {"text": "said", "start": 7, "end": 11, "upos": "VERB",
                                                "lemma": "say", "feats": None, "deprel": "root", "head": 0}
    assert client.get("/health").json() == {"status": "ok", "languages": ["en"]}


def test_service_rejects_unknown_language(tmp_path):
    client = TestClient(create_app(tmp_path, pipeline_factory=fake_pipeline))
    assert client.post("/parse", json={"text": "x", "lang": "fr"}).status_code == 422


def test_http_client_round_trip(tmp_path):
    app = create_app(tmp_path, pipeline_factory=fake_pipeline)
    client = HttpStanzaClient("http://testserver", http=TestClient(app))  # TestClient is an httpx.Client
    assert client.parse(POLICE, "en") == POLICE_PARSE


def test_multi_word_token_words_carry_token_text_and_span():
    from types import SimpleNamespace as NS
    from weave_extractors.stanza_service import to_sentences
    dar = NS(text="dar", upos="VERB", lemma="dar", feats=None, deprel="root", head=0)
    se = NS(text="se", upos="PRON", lemma="él", feats=None, deprel="iobj", head=1)
    tok = NS(text="dárselo", start_char=8, end_char=15, words=[dar, se])
    plain = NS(text="decidió", start_char=0, end_char=7,
               words=[NS(text="decidió", upos="VERB", lemma="decidir", feats=None, deprel="root", head=0)])
    [sent] = to_sentences(NS(sentences=[NS(tokens=[plain, tok])]))
    assert [(w.text, w.start, w.end, w.lemma) for w in sent.words] == [
        ("decidió", 0, 7, "decidir"), ("dárselo", 8, 15, "dar"), ("dárselo", 8, 15, "él")]
