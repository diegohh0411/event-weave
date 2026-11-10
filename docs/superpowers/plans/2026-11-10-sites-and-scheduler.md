# Step 2 — Sites and scheduler

Spec: `docs/superpowers/specs/2026-11-10-library-and-sites-design.md` (delivery step 2).
Branch: `feat/sites-and-scheduler`. Builds on step 1 (#15: `article_id`, `version`,
`superseded_at`, `run_documents`).

## Outcome

Sites are registered in the DB and edited in the web app. The crawl worker queues a check
for every enabled site when it is due. Checks upsert into the library: new URLs add
documents, a known URL whose sitemap/feed date moved past our fetch date is re-fetched and
gets a new version if its normalized text changed. Each check reports
"N new, N edited, N unchanged, N removed" and the site page shows that history. A
"Check now" button replaces the one-off crawl form.

## Decisions for this step

- **Site = outlet.** `sites.name` is the outlet key written to `documents.outlet`
  (unique). `sites.domain` unique.
- **YAML → DB once.** `weave sites import outlets.yaml` inserts sites whose name/domain
  isn't already present and never overwrites. After that the DB is the source of truth.
  `weave crawl <yaml>` (local CLI) keeps working unchanged.
- **Manual crawls.** The web "new crawl" form goes away; "Check now" on a site queues a
  crawl with `trigger='manual'`. `POST /crawls` stays in the API for scripts/back-compat,
  with `trigger='adhoc'`. Old crawl pages keep rendering.
- **When a known URL is re-fetched in step 2:** only when its discovery date
  (`lastmod` / news publication date / feed updated) is later than
  `crawled_pages.fetched_at`. Everything else is reported `existing` without a request.
  The decaying recheck schedule is step 4.
- **Edit = normalized text change.** `normalize(title) + "\n" + normalize(text)` where
  normalize = NFKC, collapse whitespace, strip. Hash compared against the latest version's
  stored hash of the same normalization (`documents.text_hash`, new column, backfilled).
  Byline/published_at changes alone update the latest row in place (not text a run used).
- **New version row:** id `"{article_id}@v{n}"` (v1 keeps the bare id, so old ids stay
  valid), same `article_id`/`external_id`... `external_id` uniqueness: the existing
  unique `(provider, external_id)` must be relaxed to `(provider, external_id, version)`.
  Previous row gets `superseded_at = now`. `crawled_pages.document_id` points at latest.
- **Removed:** a re-fetch of a stored URL returning 404/410 → article status `removed`,
  `crawled_pages.status='gone'` (new allowed value), `crawled_pages.gone_at`. The
  document is kept (library shows it; badges are step 4). Dropping out of a sitemap is
  never "removed".
- **Content-hash duplicates** must ignore rows of the same `article_id`.
- **Daily cap** = max *new* documents stored per site per UTC day, counted from
  `crawl_articles` (status `stored`) across that day's crawls of the site. The existing
  `per_day` (newest N URLs per day in discovery) stays as a discovery filter.
- **Polite limits, shared across concurrent jobs:** table `domain_throttle(domain pk,
  next_request_at)`. Before every request to a domain the fetcher atomically reserves a
  slot: `UPDATE ... SET next_request_at = greatest(next_request_at, now()) + interval
  RETURNING next_request_at - interval` and sleeps until it. `max_parallel` enforced with
  session advisory locks `pg_try_advisory_lock(hashtext(domain), k)` for k in
  `0..max_parallel-1` held around the request (wait/poll if none free). Both apply to
  every fetch, including meta/robots fetches, to the site's domain.
- **Scheduler:** `schedule_due_sites(engine, now)` in `crawl/scheduler.py`: in one
  transaction, `SELECT ... FROM sites WHERE enabled AND next_check_at <= now FOR UPDATE
  SKIP LOCKED`; skip a site that already has a queued/running crawl; insert a crawl_run
  (`site_id`, `trigger='schedule'`, request built from the site row, `days=1`); set
  `last_scheduled_at=now`, `next_check_at = now + check_every`. The worker calls it at
  most once every `WEAVE_SCHEDULER_TICK_SECONDS` (default 60) between claims. Restart-safe
  because all state is in Postgres; several workers can tick concurrently.

## Tasks

Each task: TDD, run the touched packages' tests
(`WEAVE_TEST_DATABASE_URL=postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres uv run pytest packages/<p>`),
commit. Containers use **podman**, never docker.

### Task 1 — Schema: sites, throttle, crawl linkage, versions
- Migration `0011_sites` + `weave_core/schema.py`:
  - `sites`: `id uuid pk`, `name text unique not null`, `domain text unique not null`,
    `language text not null`, `sitemaps jsonb not null default []`, `feeds jsonb not
    null default []`, `per_day int null`, `enabled bool not null default true`,
    `check_every_minutes int not null default 60 (check >= 5)`, `daily_cap int not null
    default 50 (check >= 1)`, `min_interval_seconds real not null default 2 (>= 0.5)`,
    `max_parallel int not null default 1 (1..4)`, `next_check_at timestamptz not null
    default now()`, `last_scheduled_at timestamptz`, `created_at`, `updated_at`.
  - `domain_throttle(domain text pk, next_request_at timestamptz not null)`.
  - `crawl_runs`: add `site_id uuid null fk sites on delete set null` (indexed) and
    `trigger text not null default 'adhoc'` check in (`adhoc`,`manual`,`schedule`).
  - `crawl_articles` status check adds `updated`, `unchanged`, `removed`.
  - `crawled_pages` status check adds `gone`; add `gone_at timestamptz`.
  - `documents`: add `text_hash text` (backfilled in Python inside the migration from
    title+text with the shared normalizer), unique `(provider, external_id)` replaced by
    unique `(provider, external_id, version)`.
- Put `normalize_for_edit(title, text) -> str` and `edit_hash(...)` in
  `weave_providers/shape.py` *and* a copy-free import path for the migration (migration
  may import from `weave_core` only — so place the function in `weave_core` and re-export
  from `weave_providers.shape`).
- Tests: migration up/down on a DB with existing crawl docs; constraint behaviour.

### Task 2 — Upsert crawls with versions and change outcomes
- `store.py`: `_prior_page` returns fetched_at too. Discovery dates already on
  `Candidate.published_at`; add `Candidate.updated_at` (lastmod / feed updated) in
  `discover.py`. Re-fetch rule as decided above; otherwise emit `existing`.
- On re-fetch of a stored page: 404/410 → `removed`; other non-200 → `error` (page stays
  stored, not marked gone); extract rejected → `unchanged` with reason
  `"refetch rejected: …"` (keep old doc). Same edit hash → `unchanged` (update byline /
  published_at in place if they changed). Different → new version (`updated`).
- Daily cap: `crawl()` accepts `caps: dict[outlet_name, remaining]`; once 0, further new
  candidates are emitted as `rejected` with reason `daily cap reached` and **not fetched**.
  Re-fetches of known URLs don't count.
- `CrawlReport` gains `updated`, `unchanged`, `removed`. `crawl_counters` maps the new
  statuses (`stored`→new in UI, keep key `stored`).
- Tests with `FixtureFetcher`: new, unchanged-not-refetched, edited-by-lastmod (v2 row,
  v1 superseded, run snapshot still points at v1), boilerplate-only whitespace change →
  unchanged, 410 → removed, cap reached, duplicate check ignores same article.

### Task 3 — Shared per-domain polite limits
- `crawl/throttle.py`: `DomainThrottle(engine, min_interval_for(domain),
  max_parallel_for(domain), clock, sleep)` with `acquire(domain)` context manager doing
  the reservation + advisory slot described above (dedicated connection per held slot).
- `PoliteFetcher` gets an optional `throttle`; when set it replaces the in-process
  interval for every request (pages, meta, robots). Worker builds it from the site row
  (ad-hoc crawls: defaults 2s/1).
- Tests: two threads fetching the same domain never start within `min_interval`; with
  `max_parallel=1` requests never overlap; different domains don't block each other.

### Task 4 — Scheduler tick and site-aware worker
- `crawl/scheduler.py`: `schedule_due_sites` (above) and `queue_check(engine, site_id,
  trigger='manual')` (raises `AlreadyQueued` when active crawl exists).
- `jobs.create_crawl` accepts `site_id`, `trigger`. Worker: when claimed crawl has
  `site_id`, load site settings → throttle limits and daily cap remaining
  (`daily_cap - stored today`). Tick scheduler in `run_crawl_worker` loop.
- `weave sites import <yaml>` CLI (in providers crawl cli, registered in core cli like
  `crawl`), prints inserted/skipped.
- Tests: due vs not-due vs disabled, no double queue with active crawl, two concurrent
  ticks queue once, next_check_at advanced, cap passed to crawl, import idempotent.

### Task 5 — API
- `routers/sites.py` + models: `GET /sites` (list with last check summary + next check),
  `POST /sites` (validation reusing `CrawlSite` rules + new knobs, 409 on duplicate
  name/domain), `GET /sites/{id}`, `PATCH /sites/{id}` (changing `check_every_minutes`
  recomputes `next_check_at = coalesce(last_scheduled_at, now) + interval`), `DELETE`
  (only if no crawls, else 409 — disable instead), `POST /sites/{id}/check` (202, 409 if
  active), `GET /sites/{id}/crawls` paged history with counters
  (new/updated/unchanged/removed/rejected/errors).
- `CrawlOut` gains `site_id`, `trigger`, and the new counters.
- Regenerate `web/openapi.json` + `pnpm gen:api`. Tests in `packages/api/tests/test_sites.py`.

### Task 6 — Web
- Sidebar: "Sites" (`i-lucide-globe`) above Crawls.
- `pages/sites/index.vue`: table — name, domain, language, on/off toggle, every, cap,
  last check ("14 new · 2 edited · 1 removed", relative time), next check, Check now.
- `pages/sites/new.vue` & `pages/sites/[id]/edit.vue` sharing `SiteForm.vue` with
  help text per knob.
- `pages/sites/[id]/index.vue`: settings summary, Check now, history table of crawls
  (link to existing crawl page), counts per crawl.
- `pages/crawls`: remove the new-crawl form; list shows site + trigger + change report;
  crawl detail shows the new statuses with badges.
- vitest for the report formatter and form validation; `pnpm build` passes.

### Task 7 — Final review and live check
- Whole-branch review; run all package suites + web tests; podman compose stack:
  import a site, Check now, verify history, edit text in a fixture → edited count.

## Out of scope (later steps)
Backfill (step 3); decaying rechecks, redirect handling, library badges/filters (step 4).
