"""Article extraction: fetched HTML to title, byline, date and plain body text (crawl4ai)."""
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

MIN_BODY_CHARS = 300
_PAYWALL_MARKERS = ("subscribe to continue", "suscríbete para seguir leyendo")
_JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


@dataclass(frozen=True)
class Extracted:
    title: str
    byline: str
    published_at: datetime | None
    text: str


@dataclass(frozen=True)
class Rejected:
    reason: Literal["paywalled", "no body"] | str


def extract(html: str, url: str, fallback_date: datetime | None) -> Extracted | Rejected:
    ld = _news_article(html)
    if _paywalled(html, ld):
        return Rejected("paywalled")
    try:
        metadata, markdown = asyncio.run(_crawl(html))
    except Exception as exc:  # crawl4ai failures become a recorded reason, not a crash
        return Rejected(f"extraction failed: {exc}")
    text = _plain(markdown)
    if len(text) < MIN_BODY_CHARS:
        return Rejected("no body")
    author = ld.get("author")
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get("name")
    title = metadata.get("og:title") or ld.get("headline") or metadata.get("title") or ""
    byline = metadata.get("author") or author or ""
    published = _parse_date(metadata.get("article:published_time") or ld.get("datePublished")) or fallback_date
    return Extracted(title.strip(), str(byline).strip(), published, text)


async def _crawl(html: str) -> tuple[dict, str]:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
    from crawl4ai.content_filter_strategy import PruningContentFilterLXML
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilterLXML()),
        verbose=False,
    )
    # raw: HTML needs no browser; the HTTP strategy keeps extraction free of playwright.
    async with AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy(), verbose=False) as crawler:
        result = await crawler.arun(url="raw:" + html, config=config)
    if not result.success:
        raise RuntimeError(result.error_message or "crawl failed")
    return result.metadata or {}, result.markdown.fit_markdown or ""


def _news_article(html: str) -> dict:
    for block in _JSONLD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            kinds = kind if isinstance(kind, list) else [kind]
            if any(k in ("NewsArticle", "Article", "ReportageNewsArticle") for k in kinds):
                return item
    return {}


def _paywalled(html: str, ld: dict) -> bool:
    if str(ld.get("isAccessibleForFree", "true")).lower() == "false":
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _PAYWALL_MARKERS)


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _plain(markdown: str) -> str:
    """Strip markdown syntax; paragraphs are joined by blank lines."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for line in block.splitlines():
            line = re.sub(r"^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", "", line)
            line = re.sub(r"(\*\*|__|\*|`)", "", line).strip()
            if line:
                lines.append(line)
        if lines:
            paragraphs.append(" ".join(lines))
    return "\n\n".join(paragraphs)
