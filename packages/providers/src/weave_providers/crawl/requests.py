import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


def _domain(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not allowed")
    if len(value) > 253 or "." not in value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ) or value.split(".")[-1].isdigit():
        raise ValueError("invalid domain")
    return value


def _source_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError("invalid source URL")
        if url.port is not None and url.port != (80 if url.scheme == "http" else 443):
            raise ValueError("nonstandard port")
        _domain(url.hostname)
        if any(ch.isspace() or ord(ch) < 32 for ch in value):
            raise ValueError("invalid source URL")
    except ValueError as exc:
        raise ValueError("invalid source URL") from exc
    return value


class CrawlSite(BaseModel):
    domain: str
    name: str | None = None
    language: Literal["en", "es", "hi", "tr", "uk"]
    per_day: int | None = Field(default=3, ge=1, le=200)
    feeds: list[str] = Field(default_factory=list, max_length=20)
    sitemaps: list[str] = Field(default_factory=list, max_length=20)

    _normalize_domain = field_validator("domain")(_domain)

    @field_validator("feeds", "sitemaps")
    @classmethod
    def validate_sources(cls, values):
        return [_source_url(value) for value in values]

    @model_validator(mode="after")
    def default_name(self):
        if self.name is None:
            self.name = self.domain
        return self


class CrawlRequest(BaseModel):
    sites: list[CrawlSite] = Field(min_length=1, max_length=20)
    days: int = Field(default=1, ge=1, le=30)

    @model_validator(mode="after")
    def unique_domains(self):
        domains = [site.domain for site in self.sites]
        if len(set(domains)) != len(domains):
            raise ValueError("duplicate domains")
        return self
