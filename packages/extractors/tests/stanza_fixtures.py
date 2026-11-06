"""Hand-written Stanza parses, in the service's JSON shape."""
from weave_extractors.stanza_client import Sentence, Word

POLICE = "Police said the protest was broken up after the attack."
_W = [  # text, upos, lemma, deprel, head
    ("Police", "NOUN", "police", "nsubj", 2), ("said", "VERB", "say", "root", 0),
    ("the", "DET", "the", "det", 4), ("protest", "NOUN", "protest", "nsubj:pass", 6),
    ("was", "AUX", "be", "aux:pass", 6), ("broken", "VERB", "break", "ccomp", 2),
    ("up", "ADP", "up", "compound:prt", 6), ("after", "ADP", "after", "case", 10),
    ("the", "DET", "the", "det", 10), ("attack", "NOUN", "attack", "obl", 6), (".", "PUNCT", ".", "punct", 2)]


def parse(text: str, words) -> list[Sentence]:
    out, pos = [], 0
    for w, upos, lemma, deprel, head in words:
        start = text.index(w, pos)
        pos = start + len(w)
        out.append(Word(text=w, start=start, end=pos, upos=upos, lemma=lemma, feats=None, deprel=deprel, head=head))
    return [Sentence(words=out)]


POLICE_PARSE = parse(POLICE, _W)
