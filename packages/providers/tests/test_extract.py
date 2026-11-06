from datetime import datetime, timezone
from pathlib import Path

import pytest

from weave_providers.crawl.extract import Extracted, Rejected, extract
from weave_providers.shape import tokenize

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://example.test/story"
DEMO_OUTLETS = ["reuters", "ap", "bbc", "elpais", "eluniversal", "efe"]
EXPECTED_SNIPPET = {
    "reuters": "replace roughly forty kilometres of ageing pipes",
    "ap": "replace roughly forty kilometres of ageing pipes",
    "bbc": "replace roughly forty kilometres of ageing pipes",
    "elpais": "sustituir unos cuarenta kilómetros de tuberías",
    "eluniversal": "sustituir unos cuarenta kilómetros de tuberías",
    "efe": "sustituir unos cuarenta kilómetros de tuberías",
}


def outlet_html(outlet: str) -> str:
    return (FIXTURES / outlet / "article.html").read_text(encoding="utf-8")


def page_html(name: str) -> str:
    return (FIXTURES / "pages" / f"{name}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("outlet", DEMO_OUTLETS)
def test_extracts_title_body_date(outlet):
    got = extract(outlet_html(outlet), URL, None)
    assert isinstance(got, Extracted)
    assert EXPECTED_SNIPPET[outlet] in got.text
    assert got.published_at is not None and got.published_at.tzinfo is not None
    assert got.title and got.byline
    assert "Privacy policy" not in got.text and "Most read" not in got.text
    assert "<" not in got.text and "](" not in got.text


def test_metadata_values():
    got = extract(outlet_html("elpais"), URL, None)
    assert got.title == "Aprobado el plan para renovar las tuberías de la ciudad"
    assert got.byline == "Lucía Fernández"
    assert got.published_at.isoformat() == "2026-11-03T18:30:00+01:00"


def test_jsonld_fills_in_when_meta_tags_missing():
    html = outlet_html("reuters")
    for tag in ('<meta property="og:title"', '<meta name="author"', '<meta property="article:published_time"'):
        start = html.index(tag)
        html = html[:start] + html[html.index(">", start) + 1 :]
    got = extract(html, URL, None)
    assert got.title == "Water board approves pipe replacement plan"
    assert got.byline == "Priya Nair"
    assert got.published_at == datetime(2026, 11, 3, 14, 20, tzinfo=timezone.utc)


def test_paywall_and_empty_rejected():
    assert extract(page_html("paywalled"), URL, None) == Rejected("paywalled")
    assert extract(page_html("empty"), URL, None) == Rejected("no body")


def test_feed_date_used_when_page_has_none():
    feed = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    got = extract(page_html("no_date"), URL, feed)
    assert isinstance(got, Extracted) and got.published_at == feed
    assert extract(page_html("no_date"), URL, None).published_at is None


def test_tokenizer_offsets_slice_back():
    text = "El País dijo: «hola». Otra frase."
    toks, sents = tokenize(text)
    assert all(text[s:e].strip() == text[s:e] and s < e for s, e in toks) and len(sents) == 2
    assert [text[s:e] for s, e in sents] == ["El País dijo: «hola».", "Otra frase."]
    assert text[toks[0][0] : toks[0][1]] == "El"
