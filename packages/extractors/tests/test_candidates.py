from ext_helpers import make_doc
from stanza_fixtures import POLICE, POLICE_PARSE, parse
from weave_extractors.candidates import candidates, mark


def test_candidates_police():
    found, misaligned = candidates(make_doc(POLICE), POLICE_PARSE)
    assert [c.text for c in found] == ["Police", "said", "protest", "broken", "attack"]
    assert misaligned == 0 and {c.sentence for c in found} == {0}


def test_stopword_lemmas_are_not_candidates():
    text = "They had things."
    sents = parse(text, [("They", "PRON", "they", "nsubj", 2), ("had", "VERB", "have", "root", 0),
                         ("things", "NOUN", "thing", "obj", 2), (".", "PUNCT", ".", "punct", 2)])
    assert candidates(make_doc(text), sents)[0] == []


def test_ukrainian_shifted_offsets_skipped_and_counted():
    text = "Вони п’ють чай."
    sents = parse(text, [("Вони", "PRON", "вони", "nsubj", 2), ("п’ють", "VERB", "пити", "root", 0),
                         ("чай", "NOUN", "чай", "obj", 2), (".", "PUNCT", ".", "punct", 2)])
    w = sents[0].words
    w[1] = w[1].model_copy(update={"start": w[1].start + 1, "end": w[1].end + 1})  # stanza normalised the apostrophe
    found, misaligned = candidates(make_doc(text, language="uk"), sents)
    assert [c.text for c in found] == ["чай"] and misaligned == 1


def test_mark_exact():
    assert mark("Police said it.", 7, 11) == "Police ⟦said⟧ it."
