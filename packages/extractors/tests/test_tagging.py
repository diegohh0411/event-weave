import sqlalchemy as sa
from ext_helpers import ctx, make_doc, store
from weave_core.schema import mention_tags, mentions
from weave_core.stages import Stage
from weave_extractors.shape import Mention
from weave_extractors.tagging.adapter import SignificanceTagger, TaggerSettings
from weave_extractors.tagging.questions import EVENT_TYPES, SIGNIFICANCE
from weave_inference.clients.stub import set_stub_script

TEXT = "Rain fell. Troops attacked the town."


def test_questions_exact():
    assert SIGNIFICANCE == "Is this a real-world occurrence that a newspaper would report as news?"
    assert EVENT_TYPES == ["conflict or attack", "disaster or accident", "politics or government", "crime or justice",
                           "economy or business", "health", "science or technology", "sports", "culture",
                           "statement or communication", "other"]


def test_tag_written_per_mention(stub_inference, db):
    doc = make_doc(TEXT)
    store(db, doc)
    m = Mention(document_id=doc.id, start=18, end=26, tok_start=4, tok_end=5, sentence=1, text="attacked",
                extractor="gold", extractor_version="1")
    with db.begin() as c:
        c.execute(sa.insert(mentions).values(m.row()))
    set_stub_script("stub", {"significance": 0.8,
                             "event_type": {"question_id": "event_type", "value": "conflict or attack",
                                            "probabilities": {"conflict or attack": 0.7, "other": 0.3}}})
    tagger = SignificanceTagger(TaggerSettings(), ctx(system_one="stub-s1"))
    upstream = {Stage.extract: {"mentions": [m.model_dump()]}}
    result = tagger.process(m.unit(doc.content_hash), upstream)
    assert result.emits is None
    assert result.output == {"significance": 0.8, "event_type": "conflict or attack",
                             "event_type_probs": {"conflict or attack": 0.7, "other": 0.3}}
    assert result.rows["mention_tags"] == [{"mention_id": m.id, "tagger": "significance", "tagger_version": "1",
                                            "deployment": "stub-s1", **result.output}]
    assert tagger.state(doc, m) == {"title": "Title", "sentence": "Troops ⟦attacked⟧ the town."}
