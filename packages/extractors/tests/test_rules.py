import pytest
from contract import run_contract
from ext_helpers import ctx, make_doc, unit
from stanza_fixtures import POLICE, POLICE_PARSE
from weave_core.adapters import AdapterFailure
from weave_extractors.rules import RulesSettings, StanzaRulesExtractor
from weave_extractors.stanza_client import FixtureStanzaClient


class Rules(StanzaRulesExtractor):
    def __init__(self, doc, client, nouns=frozenset({"protest", "attack"})):
        super().__init__(RulesSettings(), ctx())
        self._doc, self._client, self._nouns = doc, client, nouns

    def load_document(self, doc_id):
        return self._doc

    def client(self):
        return self._client

    def event_nouns(self, lang):
        return self._nouns


def fixture_client(tmp_path, doc, sentences):
    client = FixtureStanzaClient(tmp_path)
    client.save(doc.text, doc.language, sentences)
    return client


def test_rules_police_sentence(tmp_path):
    doc = make_doc(POLICE)
    result = Rules(doc, fixture_client(tmp_path, doc, POLICE_PARSE)).process(unit(doc), {})
    assert [m["text"] for m in result.output["mentions"]] == ["said", "protest", "broken up", "attack"]
    assert all(m["probability"] is None for m in result.output["mentions"])


def test_particle_not_contiguous_is_not_joined(tmp_path):
    text = "They broke it up."
    from stanza_fixtures import parse
    sents = parse(text, [("They", "PRON", "they", "nsubj", 2), ("broke", "VERB", "break", "root", 0),
                         ("it", "PRON", "it", "obj", 2), ("up", "ADP", "up", "compound:prt", 2),
                         (".", "PUNCT", ".", "punct", 2)])
    doc = make_doc(text)
    result = Rules(doc, fixture_client(tmp_path, doc, sents)).process(unit(doc), {})
    assert [m["text"] for m in result.output["mentions"]] == ["broke"]


def test_misaligned_stanza_offsets_are_counted(tmp_path):
    doc = make_doc(POLICE)
    shifted = [s.model_copy(update={"words": [w.model_copy(update={"start": w.start + 1, "end": w.end + 1})
                                               for w in s.words]}) for s in POLICE_PARSE]
    result = Rules(doc, fixture_client(tmp_path, doc, shifted)).process(unit(doc), {})
    assert result.output["mentions"] == [] and result.output["misaligned"] == 4


def test_unknown_language_fails_non_retryable(tmp_path):
    doc = make_doc(POLICE, language="fr")
    with pytest.raises(AdapterFailure) as err:
        Rules(doc, FixtureStanzaClient(tmp_path)).process(unit(doc), {})
    assert err.value.retryable is False


def test_rules_contract(tmp_path):
    doc = make_doc(POLICE)
    client = fixture_client(tmp_path, doc, POLICE_PARSE)
    run_contract(lambda d: Rules(d, client), [doc])


def test_event_nouns_files_exist_for_every_language():
    from weave_extractors.event_nouns import load_event_nouns
    for lang in ("en", "es", "hi", "tr", "uk"):
        assert isinstance(load_event_nouns(lang), frozenset)
