"""The `stanza` compose service: Stanza pipelines behind POST /parse (install extra `stanza`)."""
import os
import threading
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from .stanza_client import LANGUAGES, Sentence, Word

STANZA_VERSION = "1.10.1"
RESOURCES_VERSION = "1.10.0"
PROCESSORS = "tokenize,mwt,pos,lemma,depparse"
NO_MWT = {"hi"}  # languages whose default package has no multi-word-token model


class ParseRequest(BaseModel):
    text: str
    lang: Literal["en", "es", "hi", "tr", "uk"]


class ParseResponse(BaseModel):
    sentences: list[Sentence]


def stanza_pipeline(model_dir: Path) -> Callable[[str], Callable[[str], list[Sentence]]]:
    def factory(lang: str):
        import stanza
        processors = PROCESSORS.replace("mwt,", "") if lang in NO_MWT else PROCESSORS
        stanza.download(lang, model_dir=str(model_dir), package="default", processors=processors,
                        resources_version=RESOURCES_VERSION)
        nlp = stanza.Pipeline(lang, dir=str(model_dir), package="default", processors=processors,
                              download_method=None, use_gpu=False)

        def parse(text: str) -> list[Sentence]:
            out = []
            for sent in nlp(text).sentences:
                words = []
                for token in sent.tokens:
                    for w in token.words:  # multi-word tokens share their token's character span
                        words.append(Word(text=w.text, start=token.start_char, end=token.end_char, upos=w.upos,
                                          lemma=w.lemma, feats=w.feats, deprel=w.deprel, head=w.head))
                out.append(Sentence(words=words))
            return out
        return parse
    return factory


def create_app(model_dir: Path, pipeline_factory=None) -> FastAPI:
    factory = pipeline_factory or stanza_pipeline(model_dir)
    pipelines: dict[str, Callable[[str], list[Sentence]]] = {}
    lock = threading.Lock()
    app = FastAPI(title="weave-stanza")

    def pipeline(lang: str):
        with lock:
            if lang not in pipelines:
                pipelines[lang] = factory(lang)
            return pipelines[lang]

    @app.post("/parse", response_model=ParseResponse)
    def parse(req: ParseRequest) -> ParseResponse:
        return ParseResponse(sentences=pipeline(req.lang)(req.text))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "languages": sorted(pipelines)}

    assert set(LANGUAGES) == set(ParseRequest.model_fields["lang"].annotation.__args__)
    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    return create_app(Path(os.environ.get("STANZA_MODEL_DIR", "/models")))
