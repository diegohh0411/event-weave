"""Broad candidate triggers shared by the model-based extractors."""
from dataclasses import dataclass

from .shape import Doc
from .stanza_client import Sentence

CANDIDATE_UPOS = {"VERB", "NOUN", "ADJ"}
# Lemmas too generic to name an event on their own. Small on purpose: the model decides the rest.
STOPWORDS = {
    "en": {"be", "have", "do", "thing", "way", "lot", "kind", "sort", "other", "such", "same", "many", "much", "more", "most"},
    "es": {"ser", "estar", "haber", "tener", "hacer", "cosa", "otro", "mismo", "mucho", "tanto"},
    "hi": {"होना", "है", "था", "करना", "रहना", "सकना", "चुकना", "बात"},
    "tr": {"ol", "et", "yap", "şey", "bu", "diğer", "çok"},
    "uk": {"бути", "мати", "річ", "інший", "такий", "багато"},
}


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    sentence: int  # index into the Stanza parse's sentences
    text: str


def candidates(doc: Doc, sentences: list[Sentence]) -> tuple[list[Candidate], int]:
    """Every non-stopword VERB/NOUN/ADJ whose Stanza offsets slice the stored text exactly; plus misaligned count."""
    stop = STOPWORDS.get(doc.language, set())
    found, misaligned = [], 0
    for i, sentence in enumerate(sentences):
        for w in sentence.words:
            if w.upos not in CANDIDATE_UPOS or (w.lemma or w.text).lower() in stop:
                continue
            if doc.text[w.start:w.end] != w.text:
                misaligned += 1
                continue
            found.append(Candidate(w.start, w.end, i, w.text))
    return found, misaligned


def mark(sentence_text: str, rel_start: int, rel_end: int) -> str:
    return f"{sentence_text[:rel_start]}⟦{sentence_text[rel_start:rel_end]}⟧{sentence_text[rel_end:]}"
