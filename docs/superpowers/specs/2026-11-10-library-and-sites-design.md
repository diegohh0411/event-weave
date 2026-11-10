# Document library, sites, and scheduled crawling

Date: 2026-11-10

## Intent

Today a crawl is a one-off job, and an ECR run is pointed at one crawl by its ID
(`ReplaySettings.crawl_id`). That ties every experiment to a single scrape and makes
growing the corpus awkward.

Instead:

- All stored documents form one **library** that can be browsed and filtered.
- **Sites** (news domains) are registered once with their own settings, and the
  system keeps them up to date on a schedule. New crawls add to the library
  incrementally.
- **Runs** pick a slice of the library, not a crawl.

The current use is a research experiment that compares runs over time. The longer-term
goal is a product, so usability and interpretability of the data matter: someone should
be able to tell what a run saw, and what changed in the library, without reading code.

## Goals

1. A library view of every stored document, filterable by site/outlet, language,
   publish date, and change status (edited / removed).
2. Run configs select documents by filter (language, outlets, date range, limit) with no
   crawl ID. At start, a run **snapshots** the exact document versions it uses, so
   re-running and comparing runs stays meaningful as the library grows. The run page
   shows e.g. "312 documents, library as of 2026-11-10".
3. A site registry with per-site settings, edited in the web app:
   - domain, language, sitemaps/feeds
   - check frequency for new articles
   - cap on new articles per check / per day
   - on/off switch
   - backfill: start date or "all" (as far back as the sitemap archive goes), pace in
     max articles per hour, pausable, with visible progress
   - polite limits: delay between requests and max parallel requests per domain. These
     apply to all traffic to that domain: new-article checks, backfill, and rechecks.
4. A scheduler that queues crawl work for each site when it's due. Crawls **upsert**:
   a new URL adds a document, and a known URL updates the existing one instead of
   duplicating it.
5. Each crawl reports what it did ("14 new, 2 edited, 1 removed"), and the site page
   shows that history.
6. **Document versions**: if an article's text changes, keep the old version. Runs that
   used the old version keep pointing at it.
7. **Rechecks** detect pages that were edited or removed after first fetch. Each article
   is re-fetched on a decaying schedule (≈1, 7, and 30 days after first seen), then
   never again. Outcomes are unchanged / edited (new version) / gone (404/410) /
   redirected away. The library badges and filters these documents ("edited 2×",
   "removed Nov 14"). Runs still include them by default.

## Non-goals

- A URL dropping out of a sitemap or feed is **not** a disappearance. News sitemaps roll
  over within a day or two, so flagging it would be noise.
- Runs that re-run themselves on a schedule. Runs stay manually started.
- Excluding edited or removed documents automatically. That's a filter the user chooses.

## Anti-goals

- Anything that makes a past run's inputs unrecoverable, such as overwriting text a run
  used or letting a run silently pick up new documents.
- Getting sites to block us. Backfill and rechecks must never bypass a site's polite
  limits. The existing 403 trouble with milenio.com is the cautionary case.

## Current state (what this builds on)

- `documents` is already one global table, unique on `(provider, external_id)`, with
  `content_hash` and `order_key`.
- `crawled_pages` (keyed by `canonical_url`) dedupes fetched pages across crawls.
- `crawl_runs` / `crawl_articles` / `crawl_sources` (migration `0008_crawls`) hold
  per-crawl state. `crawl/jobs.py` claims queued crawls from Postgres with leases and
  `SKIP LOCKED`. The long-lived `crawler` compose service runs `weave crawl-worker`.
- `ReplayProvider` (`packages/providers/src/weave_providers/replay.py`) already treats
  `crawl_id` as optional and filters by language, outlet, date, and limit.
- Outlets are currently defined in a YAML file (`outlets.example.yaml`) and passed to a
  crawl request.

## Approaches considered

1. **Scheduler loop inside the existing crawl worker (chosen).** A `sites` table holds
   settings and next-due times. The worker, which already claims crawl jobs from
   Postgres, also ticks about once a minute: it queues work for due sites and runs
   backfill and recheck work through the same queue. Nothing new to deploy, schedules
   are editable from the web app, and all state lives in the database, so restarts are
   safe.
2. **System cron or a podman timer calling a CLI.** Simple, but schedules live outside
   the app and can't be edited from the site page. Rejected because of the product goal.
3. **A dedicated job framework (Celery beat, APScheduler).** Mature, but it adds a broker
   or a second process for work Postgres already handles here. Rejected as unneeded
   weight.

The user picked approach 1.

## Delivery order

Each step ships and is usable on its own:

1. **Library and run snapshots**: library page and API, document versioning, filter-based
   run selection with a snapshot recorded at run start, and removal of crawl-ID selection
   from the run UI.
2. **Sites and scheduler**: `sites` table plus web CRUD, the scheduler tick in the
   worker, upsert crawls, per-crawl change reports, and site history.
3. **Backfill**: sitemap-archive discovery back to a date or "all", paced per site,
   pausable, with progress shown.
4. **Rechecks**: the decaying recheck schedule, outcome recording, and library
   badges/filters.

## Decided

- Approach 1. Postgres is the only scheduler state.
- Snapshot granularity is document **version**, not just document ID.
- Polite limits are per domain and shared by every kind of traffic to that domain.
- Backfill runs alongside regular checks, so new articles never wait behind history.
- Recheck schedule ≈ 1 / 7 / 30 days **after fetch date**, then stop (exact intervals
  can be a setting). This applies to backfilled articles too. Each site has a toggle,
  "recheck backfilled articles", so heavy backfills can opt out.
- **Re-running**: "Re-run" reuses the run's frozen snapshot (exact replay). A separate
  "Re-run on current library" applies the same filter to today's library and creates a
  new run with its own snapshot. The run page links the two and shows the document diff
  (added / removed / new versions).

## Calls made by the designer (check these)

- **What counts as an edit:** a new version is created only when the normalized title or
  body text changes. Boilerplate like ads, related-article blocks, counters and timestamps
  must not trigger one. Changes to byline or publish date are recorded on the document but
  don't create a new text version.
- **Site vs. outlet:** one site is one outlet with one primary domain. The outlet name
  stays the key that run filters and `ReplayProvider` use. Extra domains or editions are
  separate sites.
- **Budgets:** the per-domain polite limits (delay between requests, max parallel) govern
  all traffic. The daily cap counts only new articles. Backfill has its own hourly pace.
  Rechecks are bounded only by the polite limits. Scheduler priority is new articles,
  then rechecks, then backfill.
- **Redirects:**
  - A redirect to the homepage, a section page or another domain counts as "removed".
  - A redirect to another article URL on the same site (a slug change) keeps the same
    document. Its URL is updated, and a new version is created only if the text changed.

## Left to the builder

- Table shapes for document versions and run snapshots, and how existing documents and
  runs migrate. Existing runs that used `crawl_id` should still resolve to their documents.
- How `sites` relates to the YAML outlets file. Suggested: import YAML into `sites` once,
  then the DB is the source of truth.
- Whether one-off manual crawls stay as a feature ("crawl this site now") or are replaced
  by a "check now" button on the site.
- How per-domain rate limiting is enforced across concurrent jobs. It has to hold even
  when a check, a backfill, and a recheck for the same domain run at once.
- Library page layout, following the existing web app's sidebar and style (#14).
