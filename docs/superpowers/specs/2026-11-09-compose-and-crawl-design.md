# One-command startup and frontend crawls

## Intent

A new user runs `podman compose up`, opens http://localhost:3000, and starts a crawl by specifying sites/domains in the frontend. No outlet YAML, host workers, manual migrations, or separate frontend process is needed. Model inference remains configurable and requires real deployments; crawling itself requires no model credentials.

PR #9 is already merged. Implement this as a follow-up PR based on current main, preserving existing API/web functionality.

## Approaches

1. Execute crawls inside API requests or API background tasks: fewer services, but browser lifetime and crawl recovery become tied to the API process.
2. Durable Postgres crawl jobs consumed by the existing crawler container: reuse the browser image and extraction code, keep HTTP requests short, persist history and progress, recover interrupted work. Selected by the implementer because it fits the existing compose and worker architecture.
3. Launch containers through the API: requires container-engine access inside the API and introduces unnecessary orchestration. Rejected.

## Startup

- A fresh checkout with Podman and its compose provider installed starts with exactly `podman compose up`. Compose builds missing images, starts Postgres, applies migrations through the API, then starts the frontend and workers after health checks.
- Publish the frontend at http://localhost:3000 (also accessible via 127.0.0.1). Keep existing localhost-only port bindings.
- Ship sensible defaults without requiring a .env file, outlet file or MCECR dataset mount. Optional environment variables supply model credentials to both API and pipeline workers; all share the shipped inference catalog.
- The crawler becomes a long-lived queue worker, not an idle container. Its browser dependencies are installed in the image. Preserve the existing `weave crawl` command.
- Missing credentials or unreachable model providers must not prevent crawling or opening the frontend. The existing deployment readiness flow explains unavailable models and prevents invalid inference runs. Do not add secret storage or a model configuration editor in this change.
- Fix current compose worker catalog/credential forwarding, and ensure startup does not require external model endpoints to respond successfully.

## Frontend experience

Navigation includes Crawls. The crawl form accepts one or more public domains/site URLs, language per site, a lookback in days and an article limit per site per day. Defaults: 1 day and 3 articles per site per day; allow larger explicit limits with bounded validation. Advanced inputs allow explicit RSS/Atom feed and sitemap URLs.

A short example of the page:

    Crawls
    Sites: [elnorte.com]  Language: [Spanish]
    Last [1] day(s)       Max articles per site/day [3]
    [Add site] [Advanced sources] [Start crawl]

    Recent crawl: Finished — 4 fetched, 0 stored
    4 articles skipped: detected paywall
    [View results]

The domain-only path tries robots.txt sitemap declarations, conventional sitemap locations, and RSS/Atom discovery links in the site's homepage. Discovery follows sources, not arbitrary article links. Explicit source URLs override or supplement automatic discovery. Failure to find a usable source produces an actionable result suggesting explicit feed/sitemap URLs. Do not promise every newspaper exposes discoverable sources.

Crawl detail shows queued/running/completed/failed status, counters (discovered, fetched, stored, duplicate/already-seen, rejected), discovered sources and errors by site, and per-article outcomes with source URL and reason. Poll while active; browser refresh retains the job. A completed crawl with zero stored articles is a clear empty result, not a generic success screen. The El Norte example should read as four fetched, zero stored, four detected paywalls. Respect existing paywall and robots policies.

Stored documents can be listed/opened from crawl detail, without a pipeline run. Provide a link/action to use crawl documents in the existing run builder. Preserve exact crawl membership (including previously stored articles encountered again), so a pipeline run from a crawl uses those documents rather than every historical document from those outlets. Pre-fill provider selection but require normal adapter/deployment validation before launching inference. No automatic model spend.

## Backend and persistence

- Add a migration for durable crawl jobs and job-specific article outcomes/document membership. Use the existing core schema/migration patterns.
- API: create crawl (HTTP 202), paged crawl history, crawl detail/progress, and paged article outcomes/documents. Requests normalize/validate sites, language, days and limits; reject empty/malformed input. Every list follows existing offset/limit rules (limit <=200).
- The dedicated worker claims queued jobs atomically. Persist progress throughout the crawl. Use leases/heartbeats and recover stale running jobs on restart. Reprocessing is safe through existing canonical URL/content deduplication; interrupted jobs must not remain stuck forever.
- Persist explicit job membership and outcomes rather than deriving history from the global crawled_pages table, which subsequent crawls update.
- Extend crawler seams with progress/cancellation-friendly callbacks as needed, while preserving CLI behavior and existing tests. Live job cancellation is deferred unless the implementation can reuse a simple existing mechanism without changing scope.
- Validate public HTTP(S) crawl targets and discovered sources, including redirects, to avoid using user input to fetch local services or cloud metadata. Handle domain normalization and www variants deliberately. Keep robots/rate limits enforced.
- OpenAPI, generated types, mock server and fixture contract tests remain consistent with production endpoints.

## Verification and acceptance

1. Fresh compose startup with no .env, no dataset and no credentials exposes healthy frontend/API and an active crawl worker.
2. A UI-submitted crawl queues, progresses and survives a page refresh; restarting the worker recovers an interrupted job.
3. Fixture-backed tests cover automatic source discovery, explicit sources, invalid input, robots failures, fetch errors, all-paywalled results, repeat crawls and job membership isolation.
4. A crawl can open stored article text and hand exactly its documents to the pipeline builder; model launch is blocked when required deployments are unavailable.
5. Existing backend package suites, web tests/typecheck, generated schema checks and container builds pass. Validate real compose startup locally; external newspaper behavior is reported separately from deterministic tests.

## Boundaries and implementer decisions

Keep Nuxt/FastAPI/Postgres, existing workers, crawl4ai and Podman compose. No hosted service, authentication, paywall bypass, scheduled crawls, arbitrary link spider, credential editor or inference-quality tuning. The implementer chooses exact table/route names, lease durations, upper input bounds, UI layout and source-discovery precedence, documenting material decisions in the plan. Routine implementation decisions do not need further approval.
