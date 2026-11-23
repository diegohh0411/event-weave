"""Local Clairic service: Cloudflare's release runtime behind POST /extract.

Runs natively on the Mac (Apple GPU), outside the containers, in its own environment, since the release
pins torch/transformers versions the workspace doesn't use:

    uv run --no-project --with-requirements services/clairic/requirements.txt \
        uvicorn --app-dir packages/enrichers/src weave_enrichers.clairic_service:app --factory --port 8002

This module imports only fastapi and pydantic at load time; torch and the release code load on startup.
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

DEFAULT_REPO = "Cloudflare/clairic-flash"
# Pinned so runs stay reproducible; bump deliberately.
DEFAULT_REVISION = "36ad68b999560f56b57ef16a22180f8cb83c6d77"


class ExtractRequest(BaseModel):
    text: str
    fields: dict[str, str]

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must be non-empty")
        return v

    @field_validator("fields")
    @classmethod
    def _fields(cls, v: dict) -> dict:
        if not v:
            raise ValueError("fields must declare at least one field")
        return v


def to_clairic(text: str, fields: dict[str, str]) -> dict:
    """Our {text, fields: {name: instruction}} -> a Clairic request where every field is a list of quotes."""
    return {"state": text,
            "fields": {name: {"type": "list", "items": "text", "instructions": instr} for name, instr in fields.items()}}


def to_spans(text: str, answer: dict) -> list[dict]:
    """One answer -> our spans. Uses Clairic's span when it quotes the text exactly. Clairic leaves the span
    null when the quote occurs more than once; then every occurrence is returned (up to MAX_OCCURRENCES) and
    the caller picks one. A quote not in the text is dropped."""
    values = answer.get("value")
    if answer.get("valid") is False or not isinstance(values, list):
        return []
    spans = answer.get("spans") or []
    confs = answer.get("confidences") or []  # per item; the list-level confidence is the fallback
    out = []
    for i, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            continue
        span = spans[i] if i < len(spans) else None
        if span is not None and text[span[0]:span[1]] != value:
            continue
        conf = confs[i] if i < len(confs) else answer.get("confidence")
        found = [span] if span is not None else _occurrences(text, value)
        out.extend({"text": value, "start": s, "end": e, "confidence": conf} for s, e in found)
    return out


MAX_OCCURRENCES = 20


def _occurrences(text: str, value: str) -> list[tuple[int, int]]:
    out, at = [], text.find(value)
    while at >= 0 and len(out) < MAX_OCCURRENCES:
        out.append((at, at + len(value)))
        at = text.find(value, at + 1)
    return out


def load_release(repo: str, revision: str, device: str, dtype: str) -> Any:
    """Download (or reuse the cached) snapshot and load Cloudflare's runtime.

    bfloat16 makes the runtime install its Triton/CUDA kernels, which don't exist on macOS; with float16 it
    keeps stock Transformers ops, which run on MPS."""
    import torch
    from huggingface_hub import snapshot_download

    path = snapshot_download(repo, revision=revision)
    sys.path.insert(0, path)
    from clairic_model import load_release_model  # type: ignore[import-not-found]

    model, _ = load_release_model(path, device=device, dtype=getattr(torch, dtype))
    return model.eval()


def create_app(load: Callable[[], Any], info: dict | None = None) -> FastAPI:
    loading = threading.Lock()

    def model():
        with loading:
            if app.state.model is None:
                app.state.model = load()
            return app.state.model

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if os.environ.get("WEAVE_CLAIRIC_LAZY") != "1":
            model()  # load before accepting requests, so /health means ready
        yield

    app = FastAPI(title="weave-clairic", lifespan=lifespan)
    app.state.busy = threading.Lock()
    app.state.model = None

    @app.post("/extract")
    def extract(req: ExtractRequest) -> dict:
        # Clairic answers one request at a time; a second one waits in the client's retry, not here.
        if not app.state.busy.acquire(blocking=False):
            raise HTTPException(503, "busy")
        try:
            out = model().predict(to_clairic(req.text, req.fields))
        except ValueError as error:  # the runtime's validation errors subclass ValueError
            raise HTTPException(422, str(error)) from error
        finally:
            app.state.busy.release()
        if out.get("truncated"):
            raise HTTPException(422, "input truncated; send fewer or shorter sections")
        answers = out.get("answers", {})
        usage = out.get("usage", {})
        return {"fields": {name: to_spans(req.text, answers.get(name, {})) for name in req.fields},
                "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "loaded": app.state.model is not None, **(info or {})}

    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    repo = os.environ.get("WEAVE_CLAIRIC_REPO", DEFAULT_REPO)
    revision = os.environ.get("WEAVE_CLAIRIC_REVISION", DEFAULT_REVISION)
    device = os.environ.get("WEAVE_CLAIRIC_DEVICE", "mps")
    dtype = os.environ.get("WEAVE_CLAIRIC_DTYPE", "float16" if device == "mps" else "bfloat16")
    return create_app(lambda: load_release(repo, revision, device, dtype),
                      info={"model": repo.rsplit("/", 1)[-1], "repo": repo, "revision": revision, "device": device, "dtype": dtype})
