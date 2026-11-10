# Library and Run Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Runs select documents from a global library by filter and freeze the exact
document versions they used, with a browsable library in the web app and two kinds of
re-run.

**Architecture:** Every `documents` row becomes an immutable **version**. New columns
group the versions of one article (`article_id`, `version`, `superseded_at`), so every
existing join on `documents.id` keeps reading the right text with no change. A new
`run_documents` table holds each run's snapshot. `ReplayProvider` writes the snapshot
the first time a run's provide job executes and reads it back on any retry. A new
`/library` API and web pages browse the latest versions. `POST /runs/{id}/rerun` copies
a run either with its snapshot or with a fresh one.

**Tech Stack:** Python 3, SQLAlchemy Core, Alembic, FastAPI, pytest against real
Postgres; Nuxt 3 + Nuxt UI, vitest, generated `openapi-typescript` client.

**Spec:** `docs/superpowers/specs/2026-11-10-library-and-sites-design.md` (step 1 of
"Delivery order")

## Global Constraints

- Snapshot granularity is document **version** (an immutable `documents.id`), not article.
- Never overwrite text a run used. Version rows are never updated except to set `superseded_at`.
- Existing runs that used `crawl_id` must still resolve to their documents.
- `config_hash` of existing runs must not change. Do not rewrite stored run configs.
- Run page copy: `"{n} documents, library as of {date}"` (date = the snapshot time, local date format).
- Re-run buttons: `"Re-run"` (same snapshot) and `"Re-run on current library"` (new snapshot).
- Containers: podman, not docker.
- Web layout follows the existing sidebar and page style (`AppSidebar.vue`, `PageHeader.vue`).

## Review Focus

1. **The provide job retries after it already wrote the snapshot.** Expected: the same
   documents are emitted, with no duplicate rows and no fresh query. Test in Task 2.
2. **The library gains documents between a run's creation and its start.** Expected: the
   snapshot reflects the library at provide time, and `snapshot_at` says so. Test in Task 2.
3. **"Re-run" on a legacy run that has no snapshot rows.** Expected: the migration
   backfilled the snapshot from that run's extract jobs, so the re-run emits the same
   documents. Test in Task 1.
4. **The library holds a superseded version.** Expected: library lists and new snapshots
   use only the latest version, while an old run still reads its old version's text.
   Test in Task 2.
5. **"Re-run on current library" for a `crawl_id` config.** Expected: the crawl filter is
   kept as configured, so the run doesn't silently widen to the whole library. The run
   page shows that it is crawl-scoped. Test in Task 4.

---

### Task 1: Schema — document versions and run snapshots

**Files:**
- Create: `packages/core/src/weave_core/migrations/versions/0009_library.py`
- Modify: `packages/core/src/weave_core/schema.py` (`documents`, `runs`, new `run_documents`)
- Modify: `packages/providers/src/weave_providers/crawl/store.py:183` (insert sets `article_id`)
- Test: `packages/core/tests/test_migration_0009.py`

**Interfaces:**
- Produces:
  - `documents.article_id: Text NOT NULL` (version 1 is the doc's own id), `documents.version: Integer NOT NULL default 1`, `documents.superseded_at: TZ NULL`, plus an index on `(article_id)` and a partial index `WHERE superseded_at IS NULL`.
  - `runs.snapshot_at: TZ NULL`.
  - `run_documents(run_id UUID FK runs.id ON DELETE CASCADE, position Integer, document_id Text FK documents.id)`, with PK `(run_id, position)` and a unique constraint on `(run_id, document_id)`.

- [ ] **Step 1: Write failing test** `test_upgrade_backfills_versions_and_legacy_snapshots`. It seeds, at revision `0008_crawls`, two documents plus a run with extract-stage document jobs (`stage='extract'`, `unit_kind='document'`) for both. It then upgrades to `0009_library` and asserts:
  - each doc has `article_id == id`, `version == 1` and `superseded_at is None`
  - `run_documents` for the run lists both ids, with positions ordered by the jobs' `order_key`, then `unit_id`
  - `runs.snapshot_at == runs.started_at`
- [ ] **Step 2: Run** `uv run pytest packages/core/tests/test_migration_0009.py -v`. Expect FAIL (no revision `0009_library`).
- [ ] **Step 3: Implement the migration and schema.** `upgrade()` adds the columns (backfill `article_id = id`, then set NOT NULL), creates `run_documents`, then inserts the snapshots of legacy runs from distinct extract-stage document jobs with `row_number()` positions, and sets `snapshot_at = started_at` for runs that got rows. `downgrade()` reverses it. In `store.py` the insert adds `article_id=doc_id`.
- [ ] **Step 4: Run** the migration test plus `uv run pytest packages/providers/tests/test_crawl.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(core): document versions and run snapshot schema`

### Task 2: Replay writes and reads snapshots

**Files:**
- Modify: `packages/providers/src/weave_providers/replay.py`
- Test: `packages/providers/tests/test_replay.py`

**Interfaces:**
- Consumes: Task 1 tables. `AdapterContext.run_id`.
- Produces: `ReplayProvider.process` emits the snapshot for `ctx.run_id`, creating it on first call. Settings stay as they are (`crawl_id` kept for legacy configs).

- [ ] **Step 1: Write failing tests**:
  - `test_replay_records_snapshot_on_first_provide`: rows in `run_documents` equal the emitted ids in order, and `runs.snapshot_at` is set.
  - `test_replay_retry_reuses_snapshot`: seed `a`, run once, seed `b`, call `process` again with the same `run_id`. Emits only `crawl:a`, and `run_documents` count is 1.
  - `test_replay_excludes_superseded_versions`: insert `crawl:a` v1 with `superseded_at` set and `crawl:a@v2` (`article_id='crawl:a'`, version 2). Emits only `crawl:a@v2`.
  - `test_old_run_keeps_old_version_text`: a run snapshotted v1, then v1 is superseded. A join from `run_documents` to `documents` still returns v1's text.

  These tests must insert a `runs` row for the context's `run_id`. Add a `make_run(engine) -> UUID` helper to the test module, and update `run_replay` to use it.
- [ ] **Step 2: Run** `uv run pytest packages/providers/tests/test_replay.py -v`. Expect the new tests to fail.
- [ ] **Step 3: Implement.** In one transaction:
  1. Lock the run row (`SELECT … FOR UPDATE`).
  2. If `run_documents` has rows for the run, emit those (joined to `documents` for `content_hash` / `order_key`, ordered by `position`).
  3. Otherwise run the existing filtered query plus `superseded_at IS NULL`, insert the snapshot rows, and set `snapshot_at = now()`.

  Keep the crawl status validation.
- [ ] **Step 4: Run** the whole replay test file. Expect PASS. `test_second_identical_run_sees_documents_added_in_between` should still pass, because each run gets its own snapshot.
- [ ] **Step 5: Commit** `feat(providers): replay snapshots run documents`

### Task 3: Library API

**Files:**
- Create: `packages/api/src/weave_api/queries/library.py`, `packages/api/src/weave_api/routers/library.py`
- Modify: `packages/api/src/weave_api/main.py` (include router), `web/openapi.json` + `web/app/api/schema.d.ts` (regenerate, as `test_openapi.py` expects)
- Test: `packages/api/tests/test_library.py`

**Interfaces:**
- Produces:
  - `LibraryFilter` query params: `outlets: list[str] | None`, `languages: list[str] | None`, `published_from / published_to: datetime | None`, `search: str | None` (case-insensitive title substring).
  - `GET /library/documents` → `Page[LibraryDocument]` (`id, article_id, version, title, outlet, language, url, published_at, versions: int`). Latest versions only, ordered by `order_key, id`, with the existing `Paging`.
  - `GET /library/documents/{document_id}` → `LibraryDocumentDetail` (summary + `byline, text, history: list[{id, version, superseded_at}]`). Any version id is accepted. 404 if unknown.
  - `GET /library/facets` → `{outlets: list[{value, count}], languages: list[{value, count}], total: int}`. It takes `LibraryFilter`, and the web run builder uses `total` as its "N documents match" preview.

- [ ] **Step 1: Write failing tests** `test_library_lists_latest_versions_with_filters`, `test_library_detail_includes_history`, `test_library_facets_counts_match_filter`, `test_library_unknown_document_404`. Assert exact ids and counts on seeded data, including one superseded version.
- [ ] **Step 2: Run** `uv run pytest packages/api/tests/test_library.py -v`. Expect FAIL with 404 routes.
- [ ] **Step 3: Implement.** The filter logic lives in `queries/library.py::library_query(f: LibraryFilter) -> Select`. `ReplayProvider` doesn't import from the API, so mirror its predicates rather than share code across packages. Regenerate the OpenAPI artifacts with the project's existing script or command, the one `test_openapi.py` names.
- [ ] **Step 4: Run** `uv run pytest packages/api -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(api): library endpoints`

### Task 4: Re-run endpoints and snapshot info on runs

**Files:**
- Modify: `packages/core/src/weave_core/runs.py` (add `RunService.rerun`), `packages/api/src/weave_api/routers/runs.py`, `packages/api/src/weave_api/queries/runs.py`
- Test: `packages/core/tests/test_runs.py` (or the existing RunService test module), `packages/api/tests/test_runs.py`

**Interfaces:**
- Produces:
  - `RunService.rerun(run_id: UUID, mode: Literal["snapshot", "current"]) -> RunCreated`. It creates a new run with an identical config and records `source_run_id` and `rerun_mode`. For `snapshot` it copies `run_documents` and sets `snapshot_at` to the source's value. For `current` it copies nothing.
  - A schema addition, made in this task's migration `0010_reruns`: `runs.source_run_id UUID NULL FK runs.id` and `runs.rerun_mode Text NULL CHECK in ('snapshot','current')`.
  - `POST /runs/{run_id}/rerun` with body `{mode}` → `CreatedOut`. The new run is in `created` state, like `POST /runs`.
  - `RunOut` gains `snapshot: {documents: int, as_of: datetime} | None`, `source_run_id`, `rerun_mode`, and `crawl_scoped: bool` (true when provide settings have `crawl_id`).
  - `GET /runs/{a}/snapshot-diff/{b}` → `{added: int, removed: int, new_versions: int}`, computed over `article_id`. `new_versions` counts articles in both runs whose version ids differ.

- [ ] **Step 1: Write failing tests**:
  - `test_rerun_snapshot_copies_documents`: the new run, after provide, emits exactly the source's ids, even after new docs were seeded.
  - `test_rerun_current_takes_fresh_snapshot`: includes the new docs.
  - `test_rerun_keeps_crawl_scope`: a `crawl_id` config re-run with `current` still emits only that crawl's documents, and `crawl_scoped` is true.
  - `test_snapshot_diff_counts`: one added, one removed, one new version.
  - `test_run_out_reports_snapshot`: checks `documents` and `as_of`.
- [ ] **Step 2: Run** these tests and expect them to fail.
- [ ] **Step 3: Implement.** A snapshot-mode re-run must skip replay's query. That already happens because Task 2 reads existing `run_documents` rows. Regenerate the OpenAPI artifacts.
- [ ] **Step 4: Run** `uv run pytest packages/core packages/api packages/providers -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat: re-run from snapshot or current library`

### Task 5: Web — library pages, run builder filters, re-run buttons

**Files:**
- Create: `web/app/pages/library/index.vue`, `web/app/pages/library/[id].vue`
- Modify:
  - `web/app/components/AppSidebar.vue`: add `{ label: 'Library', icon: 'i-lucide-library', to: '/library' }` before Crawls.
  - `web/app/pages/index.vue`: remove the `crawl_id` query path and the "Using crawl" panel. Replay settings come from the form, minus `crawl_id`. Show the "N documents match" preview from `/library/facets`.
  - `web/app/utils/schemaForm.ts`: add a way to hide fields. Use it to hide `crawl_id`.
  - `web/app/pages/runs/[id]/index.vue`: snapshot line, both re-run buttons, a link to the source run, the diff against the source when `rerun_mode == 'current'`, and a "crawl-scoped" badge.
  - `web/app/pages/crawls/[id]/index.vue`: replace "start a run with this crawl" with "View in library", which links to `/library?outlets=…` for the crawl's outlets.
- Test: `web/tests/` (vitest, following existing patterns), with mock data in `web/mock` as the existing tests use it.

**Interfaces:**
- Consumes: Task 3 and Task 4 endpoints, through the regenerated `schema.d.ts`.

- [ ] **Step 1: Write failing vitest tests**:
  - The library page renders rows and keeps filters in the URL query.
  - The run builder no longer reads `crawl_id` and shows the match count.
  - The run page shows `"312 documents, library as of …"` and both buttons, and each button posts the right `mode`.
  - The schema form hides `crawl_id`.
- [ ] **Step 2: Run** `cd web && pnpm vitest run`. Expect the new tests to fail.
- [ ] **Step 3: Implement** the pages with Nuxt UI tables and filters, following `pages/runs/index.vue` and `pages/crawls/index.vue`. Library filters (outlet and language multiselects from facets, a date range, title search) sync to `route.query`. The detail page reuses `DocumentReader.vue` and lists the version history.
- [ ] **Step 4: Run** `pnpm vitest run && pnpm nuxi typecheck`. Expect PASS.
- [ ] **Step 5: Commit** `feat(web): library pages, snapshot runs and re-run`

### Task 6: End-to-end check

- [ ] **Step 1:** `podman compose up -d --build`, then apply migrations the way the README documents.
- [ ] **Step 2:** In the browser:
  1. Open Library and filter by an outlet.
  2. Create a run from the builder with replay filters.
  3. Start it, and check the run page shows "N documents, library as of …".
  4. Click "Re-run" and confirm the same N.
  5. Click "Re-run on current library" and confirm the diff line renders.
- [ ] **Step 3:** Fix anything found, re-run the full test suites, and commit.
