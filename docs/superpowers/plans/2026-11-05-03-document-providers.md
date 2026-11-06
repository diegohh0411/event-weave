# 03 · Document Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `weave_providers` package with three `provide`-stage adapters (`mcecr`, `replay`, and the crawl store behind `weave crawl`) that put documents into `documents` in one shape, with MCECR gold in separate gold tables.

**Architecture:** Each provider is a core `StageAdapter` for `Stage.provide` (input unit `run`) that returns `StageResult(documents=..., emits=[UnitRef(document, ...)])`; core's queue already inserts documents and fans out jobs. Core gains `DocumentRecord.order_key`, a `documents.order_key` column, and a generic `StageResult.rows` so a provider can write gold rows in the same transaction without core knowing about gold. The crawler is not a run adapter: `weave crawl` discovers URLs from sitemaps/feeds, fetches politely, extracts with crawl4ai and stores documents under provider `crawl`; the `replay` adapter feeds stored documents into runs. Network and crawl4ai's browser sit behind a `Fetcher` protocol so every test uses saved fixtures.

**Tech Stack:** Python 3.12, SQLAlchemy 2.1 + Alembic (core chain), Pydantic 2, Typer, crawl4ai (optional extra `crawl`), feedparser, stdlib `urllib.robotparser`, `gzip`, `xml.etree`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-mvp/03-document-providers.md` (with `00-overview.md`, `01-core.md`; prior plans `2026-11-05-01-core.md`, `2026-11-05-02-inference.md`).

## Global Constraints

- Package `weave-providers` in `packages/providers`; depends on `weave-core` only (crawl4ai and feedparser under extra `crawl`). Adapters registered via entry-point group `weave.adapters`.
- Stages see only the document shape: stable external ID, language, title, byline, outlet, URL, publication time, text, token offsets, sentence offsets, order key, content hash. **No column of `documents` may reveal topic, split or gold** — including `id` and `external_id`.
- `content_hash = sha256(text.encode()).hexdigest()`.
- Order key: MCECR = seeded interleave across topics, file order within a topic; crawl/replay of crawl = `(published_at, url)`; replay = the original documents' order keys.
- MCECR dataset path is the setting `WEAVE_MCECR_PATH`; mounted read-only into containers at `/data/mcecr`; never copied into the repo.
- Gold is written only to `gold_*` tables, never to `documents` or `mentions`.
- Crawl: window default last 7 days; URLs only from sitemaps/feeds, never followed links; robots.txt respected; per-domain rate limit default 1 request / 2 s; user agent `EventWeaveBot/0.1 (+https://github.com/diegohh0411/event-weave)` — names no person.
- Failed / paywalled / empty pages are recorded with a reason, never stored as documents.
- No test touches the network. Crawler tests use saved HTML fixtures per outlet under `packages/providers/tests/fixtures/<outlet>/`.
- Tests use the real Postgres from the core plan.

**Data facts (checked 2026-11-05 against `~/Documents/crossdoc_event_coreference`):**
- `events/<topic>.json` is a JSON array of tokens `{text, span:[start,end], label, sent_ind, doc_ind, id?}`. `span` offsets are **topic-file-wide** (doc 1 starts mid-file), so per-document offsets are `span - first_token_of_doc.span[0]`. `B-Event` and `I-Event` tokens of one mention share `id` (e.g. `T19`); ids are scoped to the topic file.
- Every pair file found is JSON-lines, keys `topic_id, doc_ids, source_id, target_id, label, …` — **except `hindi/crossDoc/*`, which has no `topic_id`, `doc_ids` or ids.** Hindi splits therefore come from `withinDoc` only; Hindi crossDoc pairs are counted in the report and not written.
- A `.DS_Store` sits at the dataset root: language discovery must only accept the five known language directories.

**Scope decision:** Tokens and sentences for crawled text come from a small regex tokenizer in this package (`\w+|[^\w\s]`, sentences split after `[.!?…]` followed by whitespace). Spec 04 may re-tokenize with Stanza; this plan only guarantees offsets that are self-consistent.

## Review Focus

1. A topic whose token spans overlap or go backwards (bad source data): text is still rebuilt, every token's `text[s:e]` still equals its token, and the document is not silently dropped. (Task 2)
2. Hindi crossDoc pairs with no topic/ids, and a topic absent from all pair files: the run report lists both, the run still succeeds. (Tasks 2, 3)
3. Two topics with the same `doc_ind` and identical text: they get different document IDs (no `on_conflict_do_nothing` collapse). (Task 3)
4. A feed/sitemap date with no timezone, or a page with no date at all: the window filter treats naive dates as UTC and falls back to the feed date; a page with no date from any source is kept with `published_at = NULL` and sorts after dated pages. (Tasks 6, 9)
5. robots.txt that returns 404 or 5xx: 404 means allow-all; 5xx or a fetch error means disallow-all for that domain for this crawl (and the reason is recorded). (Task 7)

---

## File structure

```
packages/providers/pyproject.toml
packages/providers/src/weave_providers/
  shape.py        build_document(...) helpers: content hash, opaque ids, regex tokenizer
  mcecr/reader.py parse topic files and pair files (pure; no DB)
  mcecr/adapter.py McecrProvider adapter: selection, interleave, gold rows, report
  replay.py       ReplayProvider adapter
  crawl/outlets.py Outlet, load_outlets(path)
  crawl/discover.py sitemap + feed parsing, window, per-day limit
  crawl/fetch.py  Fetcher protocol, Crawl4aiFetcher, FixtureFetcher, PoliteFetcher (robots + rate limit)
  crawl/extract.py extract(html, url, hint) -> Extracted | Rejected
  crawl/store.py  dedup + persist pages and documents
  crawl/cli.py    `weave crawl` Typer command
packages/core/src/weave_core/adapters.py   DocumentRecord.order_key; StageResult.rows
packages/core/src/weave_core/queue.py      inserts order_key and rows
packages/core/src/weave_core/schema.py     documents.order_key; gold_documents, gold_mentions, gold_pairs, crawled_pages
packages/core/src/weave_core/migrations/versions/0003_providers.py
packages/core/src/weave_core/cli.py        mounts `crawl` when weave_providers[crawl] is installed
compose.yaml, Dockerfile, .env.example, outlets.example.yaml
```

---

### Task 1: Core: order key, generic rows, gold and crawl tables

**Files:**
- Modify: `packages/core/src/weave_core/{adapters,queue,schema}.py`, `packages/core/tests/fakes.py`
- Create: `packages/core/src/weave_core/migrations/versions/0003_providers.py`
- Test: `packages/core/tests/test_schema.py`, `packages/core/tests/test_queue.py`, `packages/api/tests/test_health.py` (head becomes `0003_providers`)

**Interfaces:**
- Produces:
  - `DocumentRecord.order_key: tuple[int, ...] = ()` (last field, defaulted)
  - `StageResult.rows: dict[str, list[dict]] = {}` — table name → rows, inserted with `ON CONFLICT DO NOTHING` in the job's completing transaction; only tables whose name starts with `gold_` are accepted, anything else raises `ValueError`.
  - Tables: `documents.order_key BIGINT[] NOT NULL DEFAULT '{}'`;
    `gold_documents(document_id PK FK documents.id, language, topic, split NULL, doc_index int)`;
    `gold_mentions(document_id FK, mention_id, start int, "end" int, text, PK(document_id, mention_id))`;
    `gold_pairs(id bigserial, language, kind ('within'|'cross'), split, topic, source_document_id, source_mention_id, target_document_id, target_mention_id, label smallint, UNIQUE(kind, source_document_id, source_mention_id, target_document_id, target_mention_id))`;
    `crawled_pages(canonical_url PK, url, outlet, fetched_at, status ('stored'|'rejected'), reason NULL, raw_html_gz bytea NULL, document_id NULL FK documents.id)`.

- [ ] **Step 1: Write failing tests**

```python
def test_documents_have_order_key_and_no_gold_columns(engine):
    cols = {c["name"] for c in sa.inspect(engine).get_columns("documents")}
    assert "order_key" in cols
    assert not cols & {"topic", "split", "gold", "label"}

def test_completed_provide_job_writes_order_key_and_gold_rows(engine, fake_run):
    # fake provider returns one document with order_key=(7,) and rows={"gold_documents":[...]}
    ...
    assert db_scalar("select order_key from documents") == [7]
    assert db_scalar("select topic from gold_documents") == "t1"

def test_rows_for_non_gold_table_are_refused(engine, fake_run):
    with pytest.raises(ValueError, match="gold_"):
        complete_with(rows={"mentions": [{}]})
```

- [ ] **Step 2: Run** `uv run pytest packages/core/tests/test_schema.py packages/core/tests/test_queue.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** fields, tables in `schema.py`, migration `0003_providers` (down_revision `0002_inference`), insert of `rows` via `schema.metadata.tables[name]` in `queue.complete` after documents. Store `order_key` as `list(document.order_key)`.
- [ ] **Step 4: Run** `uv run pytest packages/core packages/api -v` — Expected: PASS
- [ ] **Step 5: Commit** `feat(core): document order key, gold and crawl tables, generic gold rows`

---

### Task 2: MCECR reader (pure parsing)

**Files:**
- Create: `packages/providers/pyproject.toml`, `src/weave_providers/{__init__,shape}.py`, `src/weave_providers/mcecr/{__init__,reader}.py`; root `pyproject.toml` (dev dep + `[tool.uv.sources]` `weave-providers`)
- Test: `packages/providers/tests/test_mcecr_reader.py`, fixtures `tests/fixtures/mcecr/<language>/{events,withinDoc,crossDoc}/…` (hand-written, 2 topics, one pair file as JSON array, one as JSON-lines, one topic with no pairs, one with overlapping spans)

**Interfaces:**
- Produces:
  - `LANGUAGES = ("english", "spanish", "hindi", "turkish", "ukrainian")`; ISO codes `{"english":"en","spanish":"es","hindi":"hi","turkish":"tr","ukrainian":"uk"}`
  - `@dataclass McecrDoc(language: str, topic: str, doc_index: int, text: str, token_offsets: list[tuple[int,int]], sentence_offsets: list[tuple[int,int]], mentions: list[GoldMention])`
  - `@dataclass GoldMention(mention_id: str, start: int, end: int, text: str)`
  - `@dataclass GoldPair(language, kind: Literal["within","cross"], split, topic, source_doc: int, source_id: str, target_doc: int, target_id: str, label: int)`
  - `read_topic(path: Path, language: str) -> list[McecrDoc]` (file order by `doc_ind`)
  - `read_pairs(path: Path) -> list[dict]` (array or JSON-lines, detected by first non-space char)
  - `read_splits(root: Path, language: str) -> tuple[dict[str, str], list[GoldPair], int]` → topic→split, pairs, count of pairs skipped for missing topic/ids
  - `file_sha256(path) -> str`
  - in `shape.py`: `content_hash(text) -> str`, `opaque_id(*parts: str) -> str` (`sha256("/".join(parts))[:20]`)

- [ ] **Step 1: Write failing tests**

```python
def test_every_token_and_mention_slices_back(fixture_root):
    for doc in read_topic(fixture_root / "english/events/t1.json", "english"):
        for (s, e), tok in zip(doc.token_offsets, tokens_of(doc)):
            assert doc.text[s:e] == tok
        for m in doc.mentions:
            assert doc.text[m.start:m.end] == m.text

def test_offsets_are_document_relative():
    docs = read_topic(T1, "english")
    assert docs[1].token_offsets[0][0] == 0

def test_multi_token_mention_is_one_span():  # B-Event + I-Event with id T19
    assert [m.text for m in doc.mentions if m.mention_id == "T19"] == ["Pearl Harbor Day"]

def test_overlapping_spans_still_round_trip(): ...  # Review Focus 1

def test_both_pair_formats(fixture_root):
    assert read_pairs(ARRAY_FILE) == read_pairs(JSONL_FILE)

def test_topic_split_and_hindi_crossdoc_skipped(fixture_root):
    splits, pairs, skipped = read_splits(fixture_root, "hindi")
    assert splits == {"h1": "train"} and skipped == 2
```

- [ ] **Step 2: Run** `uv run pytest packages/providers/tests/test_mcecr_reader.py -v` — Expected: FAIL
- [ ] **Step 3: Implement.** Rebuild text per document: keep the source gap between consecutive tokens (`' ' * gap`), with gap 0 kept as 0; if a token's start is before the previous token's end (overlap/backwards), insert one space and continue from the current cursor. Mention span = first token start to last token end of tokens sharing `id`. Sentence offsets from `sent_ind` changes. Split conflict (topic in two splits) → take the first in order `test, dev, train` and record it; never raise.
- [ ] **Step 4: Run** tests — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): MCECR reader`

---

### Task 3: MCECR provider adapter

**Files:**
- Create: `src/weave_providers/mcecr/adapter.py`; `packages/providers/pyproject.toml` entry point `mcecr = "weave_providers.mcecr.adapter:McecrProvider"`
- Test: `packages/providers/tests/test_mcecr_adapter.py`

**Interfaces:**
- Consumes: Task 2 reader; core `AdapterSpec`, `DocumentRecord(order_key=…)`, `StageResult(rows=…)`, `UnitRef(partition_hint=topic)`
- Produces:
  - `McecrSettings(path: str = "${WEAVE_MCECR_PATH}", languages: list[str] = ["english"], splits: list[Literal["train","dev","test"]] = ["test"], topic_limit: int | None = None)`
  - `McecrProvider.spec = AdapterSpec(name="mcecr", version="1", stage=Stage.provide, input_kind=UnitKind.run, settings_model=McecrSettings)`
  - `interleave(topics: dict[str, list[T]], seed: int) -> list[T]`
  - Output (`StageResult.output`): `{"files": {relpath: sha256}, "topics_without_split": {lang: [topic]}, "skipped_pairs": {lang: n}, "documents": n}`
  - Document fields: `provider="mcecr"`, `external_id=opaque_id(language, topic, str(doc_index))`, `language=iso`, `title=""`, `byline=""`, `outlet=""`, `url=""`, `published_at=None`.

- [ ] **Step 1: Write failing tests**

```python
def test_same_seed_same_order_other_seed_differs(): 
    assert ids(run(seed=1)) == ids(run(seed=1)) != ids(run(seed=2))

def test_interleave_keeps_file_order_within_topic_and_rarely_repeats_topic():
    out = interleave({"a": [1,2,3], "b": [4,5,6], "c": [7,8,9]}, seed=0)
    assert [x for x in out if x < 4] == [1,2,3]
    assert all(topic(x) != topic(y) for x, y in zip(out, out[1:]))

def test_documents_reveal_no_topic_or_split(result):
    for d in result.documents:
        assert "t1" not in d.id + d.external_id and "test" not in d.external_id

def test_gold_rows_and_partition_hint(result):
    assert {r["topic"] for r in result.rows["gold_documents"]} == {"t1"}
    assert all(u.partition_hint == "t1" for u in result.emits)

def test_unsplit_topic_excluded_and_reported(result):
    assert result.output["topics_without_split"] == {"english": ["t3"]}

def test_identical_text_in_two_topics_gets_two_ids(): ...  # Review Focus 3
```

- [ ] **Step 2: Run** `uv run pytest packages/providers/tests/test_mcecr_adapter.py -v` — Expected: FAIL
- [ ] **Step 3: Implement.** `interleave`: `random.Random(seed)` shuffles topic order, then round-robin one document per topic until all are exhausted. Order key `(i,)` in that sequence; `UnitRef.order_key` equals it. `topic_limit` keeps the first N topics after the seeded shuffle. Gold mention/pair rows reference opaque document IDs. Unknown language in settings → `AdapterFailure(retryable=False)`.
- [ ] **Step 4: Run** tests, then `uv run pytest packages/core -v` with a run using `mcecr` on fixtures end-to-end (provide → fake extract) — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): MCECR provider with seeded interleave and gold rows`

---

### Task 4: MCECR acceptance against the real dataset

**Files:**
- Test: `packages/providers/tests/test_mcecr_dataset.py` (marker `mcecr`, skipped unless `WEAVE_MCECR_PATH` is set; registered in root `pyproject.toml` `markers`)

- [ ] **Step 1: Write the tests**

```python
PAPER_DOCS = {"english": 2600, "spanish": 694, "turkish": 193, "ukrainian": 1123, "hindi": 1192}

@pytest.mark.parametrize("lang", LANGUAGES)
def test_document_counts_match_paper(lang): assert count_docs(lang) == PAPER_DOCS[lang]

@pytest.mark.parametrize("lang", LANGUAGES)
def test_token_and_sentence_counts_match_files(lang): ...

@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_gold_mention_slices_back(lang): ...
```

- [ ] **Step 2: Run** `WEAVE_MCECR_PATH=~/Documents/crossdoc_event_coreference uv run pytest -m mcecr -v` — Expected: PASS (fix the reader, not the expected numbers, on mismatch)
- [ ] **Step 3: Commit** `test(providers): MCECR dataset acceptance`

---

### Task 5: Replay provider

**Files:**
- Create: `src/weave_providers/replay.py`; entry point `replay`
- Test: `packages/providers/tests/test_replay.py`

**Interfaces:**
- Produces: `ReplaySettings(provider: str = "crawl", languages: list[str] | None = None, outlets: list[str] | None = None, published_from: datetime | None = None, published_to: datetime | None = None, limit: int | None = None)`; `ReplayProvider.spec.name = "replay"`, version `"1"`. Returns `StageResult(output={"documents": n}, emits=[...], documents=[])` — never re-inserts.

- [ ] **Step 1: Write failing tests**

```python
def test_replay_emits_stored_documents_in_original_order(engine):
    seed_docs(order_keys=[(3,), (1,), (2,)])
    assert [u.order_key for u in run_replay().emits] == [(1,), (2,), (3,)]

def test_replay_filters_and_writes_no_documents(engine):
    assert run_replay(languages=["es"]).documents == []

def test_replay_of_unknown_provider_is_empty_not_error(engine): ...
```

- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** a query on `documents` ordered by `order_key, url, id`; emitted `UnitRef(document, id, content_hash, order_key)`, no `partition_hint` (replay is realistic-track only; a `paper` run with replay fails via core's existing "paper track needs a topic" error — test that message).
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): replay provider`

---

### Task 6: Outlets and URL discovery

**Files:**
- Create: `src/weave_providers/crawl/{__init__,outlets,discover}.py`, `outlets.example.yaml` (repo root)
- Test: `packages/providers/tests/test_discover.py`, fixtures `tests/fixtures/<outlet>/{sitemap.xml,sitemap_index.xml,feed.rss,feed.atom}`

**Interfaces:**
- Produces:
  - `Outlet(name: str, domain: str, language: str, sitemaps: list[str] = [], feeds: list[str] = [], per_day: int | None = None)`; at least one of sitemaps/feeds required.
  - `load_outlets(path: Path) -> list[Outlet]` (YAML key `outlets:`)
  - `Candidate(url: str, outlet: str, published_at: datetime | None, title: str | None)`
  - `discover(outlet: Outlet, fetcher: Fetcher, window: tuple[datetime, datetime]) -> list[Candidate]`
  - `canonical_url(url: str) -> str` (lowercase host, drop fragment, drop `utm_*`, `fbclid`, `gclid`, trailing `/`)

- [ ] **Step 1: Write failing tests**

```python
def test_news_sitemap_dates_and_window(): ...      # news:publication_date preferred, else lastmod
def test_sitemap_index_followed_one_level(): ...
def test_rss_and_atom(): ...
def test_naive_dates_are_utc(): ...                # Review Focus 4
def test_off_domain_urls_dropped(): ...
def test_per_day_limit_keeps_newest_per_day(): ...
def test_canonical_url_strips_tracking():
    assert canonical_url("https://EX.com/a/?utm_source=x&id=2#top") == "https://ex.com/a?id=2"
```

- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** sitemaps with `xml.etree` (namespaces `sitemap/0.9` and `schemas/sitemap-news/0.9`), feeds with `feedparser`. Candidates deduped by `canonical_url`; candidates with no date are kept only from sitemaps/feeds that gave no dates at all.
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): outlets and sitemap/feed discovery`

---

### Task 7: Polite fetching

**Files:**
- Create: `src/weave_providers/crawl/fetch.py`
- Test: `packages/providers/tests/test_fetch.py`

**Interfaces:**
- Produces:
  - `USER_AGENT = "EventWeaveBot/0.1 (+https://github.com/diegohh0411/event-weave)"`
  - `Fetched(url: str, status: int, html: str | None, error: str | None)`
  - `class Fetcher(Protocol): def fetch(self, url: str) -> Fetched`
  - `FixtureFetcher(routes: dict[str, Fetched | Path])` — unknown URL raises `AssertionError` (proves no stray fetch)
  - `Crawl4aiFetcher()` — crawl4ai `AsyncWebCrawler` with `BrowserConfig(user_agent=USER_AGENT)`, run via `asyncio.run` per batch; imported lazily
  - `PoliteFetcher(inner: Fetcher, min_interval: float = 2.0, clock=time.monotonic, sleep=time.sleep)` with `allowed(url) -> bool` and `fetch(url) -> Fetched`; `fetch` of a disallowed URL returns `Fetched(status=0, error="robots disallowed")` without calling `inner`.

- [ ] **Step 1: Write failing tests**

```python
def test_disallowed_url_never_reaches_inner():
    inner = FixtureFetcher({"https://ex.com/robots.txt": robots("Disallow: /private")})
    PoliteFetcher(inner).fetch("https://ex.com/private/a")
    assert inner.calls == ["https://ex.com/robots.txt"]

def test_robots_404_allows_all_5xx_disallows_all(): ...   # Review Focus 5
def test_per_domain_interval(fake_clock):
    # two fetches same domain -> sleep(2.0); different domains -> no sleep
def test_robots_fetched_once_per_domain(): ...
```

- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** with `urllib.robotparser.RobotFileParser.parse(lines)` checked against `USER_AGENT`'s product token `EventWeaveBot`.
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): robots-aware rate-limited fetcher`

---

### Task 8: Extraction and crawled document shape

**Files:**
- Create: `src/weave_providers/crawl/extract.py`; add `tokenize(text) -> tuple[list[tuple[int,int]], list[tuple[int,int]]]` to `shape.py`
- Test: `packages/providers/tests/test_extract.py`, fixtures: one saved article HTML per demo outlet (Reuters, AP, BBC, El País, El Universal, EFE) plus `paywalled.html`, `empty.html`, `no_date.html`

**Interfaces:**
- Produces:
  - `Extracted(title: str, byline: str, published_at: datetime | None, text: str)`
  - `Rejected(reason: Literal["paywalled", "no body"] | str)`
  - `extract(html: str, url: str, fallback_date: datetime | None) -> Extracted | Rejected`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("outlet", DEMO_OUTLETS)
def test_extracts_title_body_date(outlet):
    got = extract(fixture(outlet), URL[outlet], None)
    assert isinstance(got, Extracted) and EXPECTED_SNIPPET[outlet] in got.text and got.published_at

def test_paywall_and_empty_rejected():
    assert extract(fixture("paywalled"), u, None) == Rejected("paywalled")
    assert extract(fixture("empty"), u, None) == Rejected("no body")

def test_feed_date_used_when_page_has_none(): ...

def test_tokenizer_offsets_slice_back():
    toks, sents = tokenize("El País dijo: «hola». Otra frase.")
    assert all(text[s:e].strip() == text[s:e] for s, e in toks) and len(sents) == 2
```

- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** with crawl4ai on the saved HTML (`"raw:" + html`, `CrawlerRunConfig(markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilter()))`), body from `fit_markdown` with markdown syntax stripped to plain text (paragraphs joined by `\n\n`). Title/byline/date from `result.metadata` (`og:title`, `author`, `article:published_time`), then JSON-LD `NewsArticle`. Paywall: known markers (`"subscribe to continue"`, `isAccessibleForFree": false`) → `paywalled`; body under 300 characters → `no body`. Pin crawl4ai's exact version in `pyproject.toml` after checking its current docs.
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(providers): article extraction with crawl4ai`

---

### Task 9: Crawl store, `weave crawl`, containers

**Files:**
- Create: `src/weave_providers/crawl/{store,cli}.py`
- Modify: `packages/core/src/weave_core/cli.py` (mount `crawl` like `inference`, only if importable), `compose.yaml`, `Dockerfile`, `.env.example`
- Test: `packages/providers/tests/test_crawl.py`

**Interfaces:**
- Consumes: Tasks 6–8; `crawled_pages`, `documents` tables
- Produces:
  - `CrawlReport(discovered: int, fetched: int, stored: int, duplicates: int, rejected: dict[str, int])`
  - `crawl(engine, outlets: list[Outlet], fetcher: Fetcher, now: datetime, days: int = 7) -> CrawlReport`
  - CLI `weave crawl <outlets.yaml> [--days 7]` prints the report.
  - Crawled documents: `provider="crawl"`, `external_id=opaque_id(canonical_url)`, `outlet=Outlet.name`, `language=Outlet.language`, `order_key=(int(published_at.timestamp()) if published_at else 2**62,)`; ties are broken by URL in Task 5's query.

- [ ] **Step 1: Write failing tests**

```python
def test_crawl_twice_adds_no_duplicates(engine):
    first = crawl(engine, OUTLETS, fixtures, NOW); second = crawl(engine, OUTLETS, fixtures, NOW)
    assert second.stored == 0 and count("documents") == first.stored

def test_same_text_two_urls_stored_once(engine): ...        # content-hash dedup
def test_rejected_pages_recorded_not_stored(engine):
    assert db_rows("select reason from crawled_pages where status='rejected'") == [("paywalled",), ("no body",)]
def test_raw_html_kept_gzipped(engine):
    assert gzip.decompress(db_scalar("select raw_html_gz from crawled_pages limit 1")).startswith(b"<")
def test_robots_disallowed_url_never_fetched(engine): ...   # FixtureFetcher asserts on stray URLs
def test_replay_after_crawl_orders_by_time_then_url(engine): ...
def test_cli_crawl_prints_report(tmp_path, monkeypatch): ...
```

- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement.** Skip candidates whose `canonical_url` is already in `crawled_pages` (any status except fetch errors, which are retried on the next crawl). Content-hash duplicate of a `crawl` document → page stored as `rejected`, reason `duplicate of <document id>`. One transaction per page. Compose: add `crawler` service built from Dockerfile target `crawler` (installs `weave-providers[crawl]` and runs `crawl4ai-setup`), `command: sleep infinity`, used with `podman compose exec crawler weave crawl /app/outlets.yaml`; worker services get volume `${WEAVE_MCECR_PATH}:/data/mcecr:ro` and env `WEAVE_MCECR_PATH=/data/mcecr`. Change the default image sync to `--all-packages` so workers can load provider adapters. Document `WEAVE_MCECR_PATH` in `.env.example`.
- [ ] **Step 4: Run** `uv run pytest packages -v` and `podman compose config` — Expected: PASS, valid config
- [ ] **Step 5: Commit** `feat(providers): weave crawl with dedup, raw HTML, crawler container`
