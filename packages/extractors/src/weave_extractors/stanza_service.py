"""The `stanza` compose service: Stanza pipelines behind POST /parse (install extra `stanza`)."""
import os
import threading
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from .stanza_client import LANGUAGES, Entity, Sentence, Word

STANZA_VERSION = "1.10.1"
RESOURCES_VERSION = "1.10.0"
PROCESSORS = "tokenize,mwt,pos,lemma,depparse"
NO_MWT = {"hi"}  # languages whose default package has no multi-word-token model
# Languages with a Stanza 1.10 NER model we use. Hindi is left out: not sure 1.10 ships one for "hi";
# add it here once checked. Requests with ner=true for other languages get the plain parse (no entities).
NER_LANGS = {"en", "es", "uk", "tr"}


class ParseRequest(BaseModel):
    text: str
    lang: Literal["en", "es", "hi", "tr", "uk"]
    ner: bool = False


class ParseResponse(BaseModel):
    sentences: list[Sentence]


def stanza_pipeline(model_dir: Path, ner: bool = False) -> Callable[[str], Callable[[str], list[Sentence]]]:
    def factory(lang: str):
        import stanza
        processors = PROCESSORS.replace("mwt,", "") if lang in NO_MWT else PROCESSORS
        if ner:
            processors += ",ner"
        stanza.download(lang, model_dir=str(model_dir), package="default", processors=processors,
                        resources_version=RESOURCES_VERSION)
        nlp = stanza.Pipeline(lang, dir=str(model_dir), package="default", processors=processors,
                              download_method=None, use_gpu=False)

        def parse(text: str) -> list[Sentence]:
            return to_sentences(nlp(text))
        return parse
    return factory


def to_sentences(parsed) -> list[Sentence]:
    """Words of a multi-word token (es "del", "dárselo") carry the token's surface text and span, so they
    align with the stored text; their own lemma and POS are kept."""
    out = []
    for sent in parsed.sentences:
        words = []
        for token in sent.tokens:
            for w in token.words:
                words.append(Word(text=token.text, start=token.start_char, end=token.end_char, upos=w.upos,
                                  lemma=w.lemma, feats=w.feats, deprel=w.deprel, head=w.head))
        ents = [Entity(start=e.start_char, end=e.end_char, type=e.type, text=e.text) for e in getattr(sent, "ents", [])]
        out.append(Sentence(words=words, entities=ents))
    return out


def create_app(model_dir: Path, pipeline_factory=None, ner_factory=None) -> FastAPI:
    factories = {False: pipeline_factory or stanza_pipeline(model_dir), True: ner_factory or stanza_pipeline(model_dir, ner=True)}
    pipelines: dict[tuple[str, bool], Callable[[str], list[Sentence]]] = {}
    lock = threading.Lock()
    app = FastAPI(title="weave-stanza")

    def pipeline(lang: str, ner: bool):
        ner = ner and lang in NER_LANGS
        with lock:
            if (lang, ner) not in pipelines:
                pipelines[(lang, ner)] = factories[ner](lang)
            return pipelines[(lang, ner)]

    @app.post("/parse", response_model=ParseResponse)
    def parse(req: ParseRequest) -> ParseResponse:
        return ParseResponse(sentences=pipeline(req.lang, req.ner)(req.text))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "languages": sorted({lang for lang, _ in pipelines})}

    assert set(LANGUAGES) == set(ParseRequest.model_fields["lang"].annotation.__args__)
    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    return create_app(Path(os.environ.get("STANZA_MODEL_DIR", "/models")))
