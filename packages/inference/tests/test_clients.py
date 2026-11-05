import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from weave_inference.clients.chat import ChatClient
from weave_inference.clients.cloudflare import CloudflareClient
from weave_inference.clients.embeddings import EmbeddingsClient
from weave_inference.clients.openrouter import OpenRouterDecisionsClient
from weave_inference.clients.systemone import SystemOneClient
from weave_inference.limits import check_limits
from weave_inference.types import (ChatRequest, Choice, Deployment, EmbeddingsRequest, ExtractionRequest, Limits,
                                   LimitExceeded, Noul, Provider, ProviderError, Score, SystemOneRequest)

FIX = Path(__file__).parent / "fixtures"


def resp(path, status=200):
    return httpx.Response(status, content=(FIX / path).read_bytes())


def prov(kind, url):
    return Provider(id=kind, kind=kind, base_url=url)


def dep(**kw):
    return Deployment(id="d", model="m", provider="p", **kw)


def s1_request():
    return SystemOneRequest(state="hi", questions=[Choice(id="tone", prompt="Tone?", options=["angry", "calm", "excited"]),
                                                   Noul(id="urgent", prompt="Urgent?"), Score(id="rating", prompt="Rate", low=0, high=2)])


def test_systemone_round_trip():
    c = SystemOneClient()
    r = c.build(dep(joint=True), prov("typesafe", "https://api.typesafe.ai"), s1_request(), "k")
    body = json.loads(r.content)
    assert str(r.url) == "https://api.typesafe.ai/v1/systemone" and r.headers["authorization"] == "Bearer k"
    assert body["model"] == "m" and body["state"] == "hi"
    assert body["questions"]["tone"] == {"type": "choice", "instructions": "Tone?", "criteria": {"angry": "angry", "calm": "calm", "excited": "excited"}}
    assert body["questions"]["rating"]["criteria"] == ["0", "1", "2"]
    out, usage = c.parse(dep(joint=True), resp("typesafe/systemone_response.json"))
    by = {a.question_id: a for a in out.answers}
    assert by["tone"].value == "calm" and by["tone"].probabilities["calm"] == 0.8
    assert by["urgent"].value == 0.98 and by["rating"].value == 1.7
    assert (usage.input_tokens, usage.output_tokens) == (120, 12)


def test_systemone_not_joint_sends_one_request_per_question():
    c = SystemOneClient()
    reqs = c.build_many(dep(joint=False), prov("llamacpp", "http://h:8080"), s1_request(), None)
    assert len(reqs) == 3 and "authorization" not in reqs[0].headers
    assert [list(json.loads(r.content)["questions"]) for r in reqs] == [["tone"], ["urgent"], ["rating"]]
    answers = [
        {"model": "m", "answers": {"tone": {"type": "choice", "choice": "calm", "probabilities": {"calm": 1.0}}}, "usage": {"input_tokens": 10, "output_tokens": 1}},
        {"model": "m", "answers": {"urgent": {"type": "noul", "noul": 0.5}}, "usage": {"input_tokens": 10, "output_tokens": 1}},
        {"model": "m", "answers": {"rating": {"type": "score", "score": 1.0}}, "usage": {"input_tokens": 10, "output_tokens": 1}},
    ]
    out, usage = c.parse_many(dep(joint=False), [httpx.Response(200, json=a) for a in answers], s1_request())
    assert [a.question_id for a in out.answers] == ["tone", "urgent", "rating"]
    assert usage.input_tokens == 30 and usage.output_tokens == 3
    assert len(c.build_many(dep(joint=True), prov("llamacpp", "http://h:8080"), s1_request(), None)) == 1


def test_score_offset_uses_low():
    c = SystemOneClient()
    req = SystemOneRequest(state="s", questions=[Score(id="rating", prompt="r", low=1, high=5)])
    assert json.loads(c.build(dep(), prov("typesafe", "http://x"), req, None).content)["questions"]["rating"]["criteria"] == ["1", "2", "3", "4", "5"]
    out, _ = c.parse(dep(), httpx.Response(200, json={"answers": {"rating": {"type": "score", "score": 2.0}}}), req)
    assert out.answers[0].value == 3.0


def test_openrouter_decisions_round_trip():
    c = OpenRouterDecisionsClient()
    r = c.build(dep(), prov("openrouter", "https://openrouter.ai/api/v1"), s1_request(), "k")
    assert str(r.url) == "https://openrouter.ai/api/v1/decisions"
    out, usage = c.parse(dep(), resp("openrouter/decisions_response.json"))
    assert out.answers[0].value == "calm"
    assert usage.input_tokens == 50 and usage.reported_cost == Decimal("0.00012")


def test_cloudflare_system_one_and_extraction():
    c = CloudflareClient()
    d = dep(settings={"account_id": "acc"})
    d = d.model_copy(update={"model": "@cf/acme/clef"})
    r = c.build(d, prov("cloudflare", "https://api.cloudflare.com/client/v4"), s1_request(), "t")
    assert str(r.url) == "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/acme/clef"
    out, usage = c.parse(d, resp("cloudflare/systemone_response.json"))
    assert out.answers[0].value == 0.9 and usage.output_tokens == 3
    ex = ExtractionRequest(text="at Foro Indie", fields={"venue": "string"})
    assert json.loads(c.build(d, prov("cloudflare", "https://x"), ex, "t").content) == {"text": "at Foro Indie", "fields": {"venue": "string"}}
    out, usage = c.parse(d, resp("cloudflare/extraction_response.json"))
    span = out.fields["venue"][0]
    assert (span.text, span.start, span.end, span.confidence) == ("Foro Indie", 10, 20, 0.95)


def test_chat_round_trip():
    c = ChatClient()
    req = ChatRequest(messages=[{"role": "user", "content": "x"}], max_tokens=50)
    r = c.build(dep(), prov("openai", "http://vllm:8000/v1/"), req, None)
    body = json.loads(r.content)
    assert str(r.url) == "http://vllm:8000/v1/chat/completions" and body["max_tokens"] == 50 and "chat_template_kwargs" not in body
    out, usage = c.parse(dep(), resp("openai/chat_response.json"))
    assert json.loads(out.text) == {"venue": "Foro Indie"}
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (100, 20, 64)


def test_chat_passes_nuextract_template_args():
    T = '{"venue": "verbatim-string"}'
    req = ChatRequest(messages=[{"role": "user", "content": "x"}], template_args={"template": T, "instructions": "be exact"})
    body = json.loads(ChatClient().build(dep(), prov("openai", "http://v/v1"), req, None).content)
    assert body["chat_template_kwargs"]["template"] == T and body["chat_template_kwargs"]["instructions"] == "be exact"


def test_embeddings_round_trip_orders_by_index():
    c = EmbeddingsClient()
    r = c.build(dep(), prov("openai", "http://v/v1"), EmbeddingsRequest(texts=["a", "b"]), "k")
    assert str(r.url) == "http://v/v1/embeddings" and json.loads(r.content)["input"] == ["a", "b"]
    out, usage = c.parse(dep(), resp("openai/embeddings_response.json"))
    assert out.vectors == [[0.1, 0.2], [0.3, 0.4]] and usage.input_tokens == 8


@pytest.mark.parametrize("client", [SystemOneClient(), OpenRouterDecisionsClient(), CloudflareClient(), ChatClient(), EmbeddingsClient()])
@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>"), httpx.Response(200, json={"unexpected": 1}), httpx.Response(200, json=[1])])
def test_unparseable_response_is_provider_error_not_crash(client, response):
    with pytest.raises(ProviderError) as e:
        client.parse(dep(), response)
    assert e.value.retryable is False


def test_requests_expose_limit_attributes():
    d = dep(limits=Limits(max_choice_options=2, context_tokens=5))
    with pytest.raises(LimitExceeded):
        check_limits(d, s1_request())
    assert ChatRequest(messages=[{"role": "user", "content": "ab"}, {"role": "user", "content": "cd"}]).text_for_context == "ab\ncd"
    assert EmbeddingsRequest(texts=["a", "b"]).text_for_context == "a\nb"
    assert ExtractionRequest(text="t", fields={}).text_for_context == "t"


def test_joint_parse_many_applies_score_offset():
    c = SystemOneClient()
    req = SystemOneRequest(state="s", questions=[Score(id="rating", prompt="r", low=1, high=5)])
    r = httpx.Response(200, json={"answers": {"rating": {"type": "score", "score": 2.0}}})
    out, _ = c.parse_many(dep(joint=True), [r], req)
    assert out.answers[0].value == 3.0
    with pytest.raises(ProviderError):
        c.parse_many(dep(joint=True), [r, r], req)


def test_over_size_request_refused_by_check_limits():
    d = dep(limits=Limits(max_request_bytes=50))
    for req in (SystemOneRequest(state="x" * 100, questions=[Noul(id="q", prompt="p")]),
                ChatRequest(messages=[{"role": "user", "content": "x" * 100}]),
                EmbeddingsRequest(texts=["x" * 100]), ExtractionRequest(text="x" * 100, fields={})):
        with pytest.raises(LimitExceeded):
            check_limits(d, req)
    check_limits(d, EmbeddingsRequest(texts=["ok"]))


def test_bad_cost_and_bad_rows_are_provider_errors():
    with pytest.raises(ProviderError):
        ChatClient().parse(dep(), httpx.Response(200, json={"choices": [{"message": {"content": "x"}}], "usage": {"cost": "abc"}}))
    with pytest.raises(ProviderError):
        EmbeddingsClient().parse(dep(), httpx.Response(200, json={"data": [1, 2]}))
