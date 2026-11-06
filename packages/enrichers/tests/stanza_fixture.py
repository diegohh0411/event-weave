"""Hand-written Stanza parses with entities, in the service's JSON shape."""
from weave_enrichers._shared import Entity, Sentence, Word

ARREST = "Police arrested two men in Madrid on Monday."
_W = [  # text, upos, lemma, deprel, head
    ("Police", "NOUN", "police", "nsubj", 2), ("arrested", "VERB", "arrest", "root", 0),
    ("two", "NUM", "two", "nummod", 4), ("men", "NOUN", "man", "obj", 2),
    ("in", "ADP", "in", "case", 6), ("Madrid", "PROPN", "Madrid", "obl", 2),
    ("on", "ADP", "on", "case", 8), ("Monday", "PROPN", "Monday", "obl:tmod", 2), (".", "PUNCT", ".", "punct", 2)]


def parse(text: str, words, entities=()) -> list[Sentence]:
    out, pos = [], 0
    for w, upos, lemma, deprel, head in words:
        start = text.index(w, pos)
        pos = start + len(w)
        out.append(Word(text=w, start=start, end=pos, upos=upos, lemma=lemma, feats=None, deprel=deprel, head=head))
    ents = [Entity(start=text.index(t), end=text.index(t) + len(t), type=ty, text=t) for t, ty in entities]
    return [Sentence(words=out, entities=ents)]


ARREST_PARSE = parse(ARREST, _W, [("Police", "ORG"), ("Madrid", "GPE"), ("Monday", "DATE")])
