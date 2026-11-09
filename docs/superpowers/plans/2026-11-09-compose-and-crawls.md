# One-command startup and frontend crawls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `podman compose up`, open localhost:3000, submit public domains and follow a durable crawl through to collected articles and optional pipeline processing.

**Architecture:** Store crawl jobs and article outcomes in Postgres. The browser-equipped crawler service consumes leased jobs, reusing discovery, politeness, extraction and deduplication. FastAPI exposes queue/history/results; Nuxt submits and polls them. Pipeline replay can select exact crawl membership.

**Tech Stack:** Existing Python 3.12/SQLAlchemy/Postgres/FastAPI/crawl4ai and Nuxt 4/TypeScript/Vitest; Podman compose.

**Spec:** `docs/superpowers/specs/2026-11-09-compose-and-crawl-design.md` (approved).

## Global Constraints

- Fresh checkout starts with exactly `podman compose up`; no required .env, outlet file, MCECR dataset, manual migration or host process.
- Keep localhost-only bindings; frontend http://localhost:3000, API port 8000.
- Crawling requires no inference credentials. Credentials stay in optional environment variables and are never returned by the API.
- Existing `weave crawl` remains compatible. No paywall bypass, scheduled crawls, arbitrary link spider or automatic model spend.
- Every list uses offset >=0 and limit 1..200, default 50; over-limit requests return 422.
- No deterministic test fetches live sites or calls real models.
- Existing gold-data restrictions remain binding.
- Preserve user method: subagent-driven execution; only Sonnet, Terra, Haiku and Luna children. Check availability before dispatch; do not silently substitute unavailable models.

## Review Focus

1. Worker killed after storing an article: reclaim the job after lease expiry without duplicate document membership or stale-owner writes (Task 1/3).
2. Zero stored articles: distinguish all-paywalled, robots blocked, discovery failed and an empty time window, including after browser refresh (Task 2/4/5).
3. Second crawl finds previously stored pages: include accessible existing documents in this crawl, exclude unrelated historical documents from replay (Task 2/4).
4. Malicious site/source/redirect resolves to a private address: refuse before fetching, including browser subrequests (Task 2).
5. No .env, no dataset, unreachable models: frontend/API/crawler start and crawling remains available; inference launch explains missing readiness (Task 6).

## Interfaces and decisions

- Status values: `queued`, `running`, `completed`, `failed`. A completed crawl can have zero documents; fatal worker/configuration errors produce failed, while per-source/article failures remain visible outcomes.
- `CrawlRequest`: `sites: list[CrawlSite]` (1..20), `days: int` (1..30, default 1). `CrawlSite`: normalized domain, name (default domain), language (en/es/hi/tr/uk), `per_day` (1..200, default 3), optional `feeds`/`sitemaps` (each <=20 public HTTP(S) URLs). Reject credentials, nonstandard ports, IP literals and malformed domains. Duplicate normalized domains are rejected.
- `crawl_runs`: UUID id; JSONB request; status; created/started/finished timestamps; UUID claim token; lease_until; JSONB progress; text error.
- `crawl_articles`: composite PK (crawl_id, canonical_url), FK crawl_id; outlet, url, status (`stored`, `existing`, `duplicate`, `rejected`, `error`), reason, document_id nullable FK, fetched bool, updated_at. Count existing/duplicate separately; document membership includes any referenced document_id, deduplicated.
- `crawl_sources`: composite PK (crawl_id, url), site/domain, kind (feed/sitemap), status and error. Keep discovery failures even when no article is found.
- Initial lease 120 seconds, heartbeat every 20 seconds; stale jobs are reclaimed with a new token. Every job-owned write checks the token. Reclaim resumes through idempotent article upserts; prior outcomes are retained and counters derived from persisted rows.
- Public URL validation uses injectable DNS resolution. Reject any non-global address (including mixed public/private DNS answers). Validate redirects and browser requests, block non-HTTP(S) network requests. Existing CLI can retain its behavior; the UI queue always enables public-target enforcement. Guard before each request, and document DNS rebinding limits if transport cannot pin resolutions.
- Discovery: explicit sources are used when supplied. Otherwise inspect robots sitemap declarations and homepage RSS/Atom links, then conventional `/sitemap.xml`, `/sitemap_index.xml`, `/feed`, `/rss` fallbacks. Deduplicate sources and cap source traversal (100 sources/job, <=2 sitemap-index levels). Keep robots checks and domain limits. All child source URLs are validated.
- Existing same-origin redirect policy may reject www/apex redirects. Permit only validated redirects within requested domain + subdomains; check each hop's robots rules. Do not relax to unrelated domains.
- Routes: POST `/crawls` -> 202 CrawlOut; GET `/crawls` -> Page[CrawlOut]; GET `/crawls/{id}` -> CrawlOut; GET `/crawls/{id}/articles` -> Page[CrawlArticleOut]; GET `/crawls/{id}/sources` -> Page[CrawlSourceOut]; GET `/crawls/{id}/documents/{did}` -> stored document body, only for that crawl's member documents, no gold/mention data.
- `CrawlOut`: id, request, status, timestamps, counters, error. Counters include discovered, fetched, stored, existing, duplicates, rejected, errors and reason counts.
- `ReplaySettings.crawl_id: UUID | None`: select distinct associated document IDs; require completed crawl, fail clearly on unknown/incomplete crawl. Intersect existing filters if supplied. Stable original document ordering remains unchanged.

### Task 1: Durable queue and migration

**Files:** modify `packages/core/src/weave_core/schema.py`; create migration `0008_crawls.py`; create `packages/providers/src/weave_providers/crawl/jobs.py` and `requests.py`; tests `packages/providers/tests/test_crawl_jobs.py`; update schema-head expectations in core/API tests.

**Interfaces:** request models as above; `create_crawl(engine, request) -> UUID`; `claim_crawl(engine, now, lease_seconds=120) -> ClaimedCrawl | None`; `heartbeat(engine, id, token, now) -> bool`; `finish_crawl(engine, id, token, error=None) -> bool`; `record_article(engine, id, token, outcome) -> bool`; `record_source(...) -> bool`. ClaimedCrawl carries id, token and validated request.

- [ ] Write tests that create a queued job, claim it once, reject concurrent claims, reclaim expired lease with new token, reject stale-owner heartbeat/article/finish, and deduplicate repeated article outcomes. Assert original article/document data survives reclaim.
- [ ] Run `uv run pytest packages/providers/tests/test_crawl_jobs.py -q`; confirm intended missing implementation failures.
- [ ] Implement migration, request normalization/bounds and atomic claims (`FOR UPDATE SKIP LOCKED`). Derive counters from rows; never trust browser-supplied counters.
- [ ] Run providers suite and relevant core schema/API health tests separately; verify migration upgrades to 0008 and existing tables remain.
- [ ] Commit `feat(crawl): persist leased crawl jobs and article outcomes`.

### Task 2: Public site discovery and observable crawl execution

**Files:** create `crawl/site_sources.py` and `crawl/public_urls.py`; modify `crawl/discover.py`, `fetch.py`, `store.py`; tests `test_site_sources.py`, `test_public_urls.py`, extend `test_crawl.py`.

**Interfaces:** `resolve_sources(site, fetcher) -> SourceResolution` with sources and per-source errors; `validate_public_url(url, resolver=...) -> str`; `crawl(..., observer=None, cancelled=None) -> CrawlReport` retains current callers. Observer receives source and article outcome events after persistence; cancellation seam stops a lost-lease worker. Previously stored document outcomes include document identity.

- [ ] Write fixture tests for robots-declared sitemap, HTML feed discovery, explicit source precedence, conventional fallback, bounded nested indexes, missing feeds, time-window exclusion, and www redirect handling.
- [ ] Write tests refusing localhost, metadata IPs, mixed DNS answers, private redirects and browser subrequests before transport. Use injected resolver/fixture browser; no live DNS.
- [ ] Write repeat-crawl test: existing article emits document identity; a duplicate-text page associates the original document; all-paywalled returns four rejected outcomes and zero stored; CLI output remains compatible.
- [ ] Run focused tests RED, implement seams and guards, then providers suite GREEN. Preserve canonical URL/content deduplication, rate limits and robots behavior.
- [ ] Commit `feat(crawl): discover public site sources and report article outcomes`.

### Task 3: Long-lived crawl worker and exact replay

**Files:** create `crawl/worker.py`; modify `crawl/cli.py`, `packages/core/src/weave_core/cli.py`, `packages/providers/src/weave_providers/replay.py`; tests `test_crawl_worker.py`, extend `test_replay.py` (use existing test filename if different).

**Interfaces:** `run_crawl_worker(engine, stop, poll_seconds=2, once=False, fetcher_factory=...)`; `process_claim(engine, claim, ...)`; CLI `weave crawl-worker`. A heartbeat loop uses its own DB connection; lost lease stops work before further event writes. Worker catches per-job exceptions, records failure, continues polling; shutdown closes browser and releases or expires claim.

- [ ] Write tests submitting a job through create_crawl, processing fixture pages, persisting intermediate progress and final status, reclaiming an interrupted job, ignoring stale token, and processing the next job after a failure.
- [ ] Write replay test: two crawls share an existing document; one has an unrelated document; crawl-specific replay yields exactly its distinct membership and stable ordering. Unknown/running crawl fails with actionable validation.
- [ ] Run focused tests RED; implement worker/CLI and replay selection; run providers suite GREEN.
- [ ] Commit `feat(crawl): consume jobs and replay exact crawl membership`.

### Task 4: Crawl API and contract

**Files:** create `packages/api/src/weave_api/routers/crawls.py`; register in main.py; add `tests/test_crawls.py`; extend no-gold sweep; regenerate `web/openapi.json` and `app/api/schema.d.ts`; add `web/mock/fixtures/crawls.ts` and mock routes/contract tests.

**Interfaces:** routes and response models defined above. Reuse get_engine, Paging, Page and existing 404 mapping. Create stores validated request only; API never starts browser or performs crawling inside a request.

- [ ] Write API tests for 202 queued response, normalized domain, malformed/empty/over-bound inputs, paging limit rejection, unknown IDs, all-paywalled result, refreshable progress, source error list, and document membership enforcement.
- [ ] Extend no-gold sweep with crawl job/document routes; assert no gold marker/keys outside scores.
- [ ] Run API tests RED; implement routes and regenerate OpenAPI with `uv run python -m weave_api.openapi`; regenerate TS using web's existing command.
- [ ] Add production-shaped mock crawl fixtures and create/history/detail/results routes; run API suite and web fixture contract tests GREEN.
- [ ] Commit `feat(api): expose crawl jobs progress and results`.

### Task 5: Crawl frontend and pipeline handoff

**Files:** add `web/app/pages/crawls/index.vue`, `crawls/[id].vue`, `crawls/[id]/documents/[did].vue`; modify app.vue and builder index.vue; add focused reusable components only if needed; tests `web/tests/pages/crawls.test.ts`; extend builder tests and mock.

**Interfaces:** use generated API types. Form permits multiple sites/languages, lookback and per-day limit; advanced explicit source fields. Navigate to crawl detail after successful POST. Poll active crawl every 2 seconds; cancel timers/unmount and ignore stale responses. Document view uses plain escaped text, title and URL, no pipeline-specific mention endpoint.

- [ ] Write page tests for submitting domains/options, validation errors, status transition via polling, refresh from route ID, four-paywall/zero-document explanation, source errors, document links and unmount cleanup.
- [ ] Write builder handoff test: `/?crawl_id=<id>` selects replay with crawl_id, realistic track, displays crawl context and requires ordinary adapter/deployment validation; does not auto-launch or spend money.
- [ ] Run tests RED; implement pages and navigation, crawl-to-builder action and a visible deployment re-probe action/error display so a cold API can configure inference after startup.
- [ ] Run full web tests, typecheck, fixture contract and generated-type checks GREEN; commit `feat(web): launch crawls and browse collected articles`.

### Task 6: One-command compose startup and end-to-end verification

**Files:** modify compose.yaml, Dockerfile, service.py/main.py as needed for startup probing, .env.example, README.md; add runtime/config tests only where they exercise behavior rather than literal source text.

**Interfaces:** crawler command `weave crawl-worker`; common inference catalog path `/app/inference.yaml` mounted read-only to API and pipeline workers; explicitly forward optional credential env variables with empty defaults. Remove required host dataset mounts from default path; opt-in MCECR mount via documented override/profile. Existing optional stanza service remains available without blocking crawl startup. Crawler waits for migrated/healthy API.

- [ ] Write API startup test using a configured catalog with unreachable provider: constructing/starting app does not perform blocking external probe, health and crawl routes work, deployment listing gives not-ready reason until explicit probe. Keep CLI probe/smoke behavior. Worker readiness initialization must still permit valid inference (probe on worker startup bounded/nonfatal or load recorded readiness deliberately; test that path).
- [ ] Run test RED; implement startup wiring and documented optional model/dataset setup. Missing credentials must neither crash workers nor block health. UI readiness remains truthful.
- [ ] Run backend suites separately per package, web suite/typecheck/schema consistency; build web/API/crawler images.
- [ ] Use an isolated compose project and alternate localhost ports (parameterize published ports if needed), with a fresh database volume and no env file/credentials/dataset, so existing user services/data are untouched. Start from missing images/build context where practical. Verify frontend HTTP 200, API health and migrated schema, crawler consuming a submitted fixture-controlled or public-site crawl, job status/results persistence, and worker restart recovery. Keep deterministic fixture-network tests separate from any live-site smoke. Remove only this verification project's containers/volumes afterward.
- [ ] Document exact URL, normal first-use steps, no-source/paywall explanations and how to enable inference. Commit `feat(compose): start the complete crawl app with one command`.

## Final review and delivery

Run one whole-branch review against approved design and plan, using Terra with fresh context and a packaged diff. Pay particular attention to lease ownership, SSRF/redirect controls, exact document membership, no-key startup, and production/mock contract parity. Fix Important/Critical issues in one wave, verify, and preserve rulings/evidence in a completion record. Create a follow-up PR against main (PR #9 is merged), link it to the thread immediately, and include fresh verification and remaining limitations. User already authorized PR work; no additional confirmation is needed to push the feature branch/create the follow-up PR.
