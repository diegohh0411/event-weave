# Spec 08 completion record

Implementation and final fix wave complete through d79d671. No push or merge performed.

## Verification

- Backend packages run separately: core 119, inference 109, providers 101, extractors 42, enrichers 96, clusterer 56, eval 95, API 57: **675 passed, 15 provider tests skipped**.
- Core emitted its existing SQLAlchemy vector reflection warning.
- Web: **140 tests passed**, typecheck passed with Node 24.22.1; generated API types unchanged.
- Podman web image build passed using pinned pnpm and Node 22.
- GitHub-hosted CI remains unverified until push.
- Host pnpm could not fetch/verify its pinned release in the restricted environment; installed local binaries ran web checks. Container installed the pinned release successfully.

## Final fixes

- 75c516a: use requested run's completed tag output for cluster facets and members.
- 63092cc: filter full enrichment population in SQL before seeded paging; count eligible population.
- d79d671: cap comparisons at eight; disable additional picks and explain over-limit URLs.

## Scoped re-review

### Finding Verdicts

- **Enrichment sample filtering and population totals** — ADDRESSED. The shared eligible population is filtered by enricher before seeded ordering and SQL pagination, and a separate count uses the same filtered base ([report.py:64](../../../packages/enrichers/src/weave_enrichers/report.py#L64), [report.py:73](../../../packages/enrichers/src/weave_enrichers/report.py#L73), [report.py:89](../../../packages/enrichers/src/weave_enrichers/report.py#L89), [enrichments.py:32](../../../packages/api/src/weave_api/routers/enrichments.py#L32)). The added tests cover sparse and unfiltered totals, paging, ordering, and empty results.
- **Cluster facets and member tags from other runs** — ADDRESSED. The tag subquery selects completed tag jobs for the requested run and joins their exact cache keys to stage outputs; both facets and members use it ([clusters.py:9](../../../packages/api/src/weave_api/queries/clusters.py#L9), [clusters.py:22](../../../packages/api/src/weave_api/queries/clusters.py#L22), [clusters.py:65](../../../packages/api/src/weave_api/queries/clusters.py#L65)). The added test seeds conflicting tags for the same mention in two runs and checks both outputs.
- **Compare selection exceeding eight runs** — ADDRESSED. URL IDs are deduplicated and capped before the API request, unchecked choices are disabled at the limit, and the page explains the limit and warns when a URL exceeds it ([compare.vue:14](../../../web/app/pages/compare.vue#L14), [compare.vue:42](../../../web/app/pages/compare.vue#L42), [compare.vue:108](../../../web/app/pages/compare.vue#L108)). Added tests cover the cap, selection below it, and an over-limit URL.

### New Breakage in the Fix Diff

None found.

### Out-of-Scope Observations

None.

### Checks

- The fix report records fresh successful API, enrichers, and web test runs, web typecheck, schema regeneration check, and web container build. The diff contains tests covering the three fixes. No suites were rerun, as requested.

### Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.**


## Rulings made during implementation

Copied verbatim from the ledger, in decision order, including costs if wrong.

- Ruling: T5 sample route takes Paging `limit` as the sample size (no separate `n`), `offset` slices the seeded order — satisfies paging guard — cost if wrong: one query param rename.

- Ruling: T7 sweep checks marker string + forbidden keys `gold`, `topic`, `split`; drop "mention_id from gold" clause — unverifiable as written — cost: slightly weaker sweep.

- Ruling: T6 compare keeps weave_eval.cost.cost_report figures (all first-stage jobs, finished-run throughput) — compare only scores finished runs, and one source matches the CLI report — cost if wrong: small throughput discrepancy vs runs page.

- Ruling: T7 sweep exempts /openapi.json from forbidden-key check only (schema names field ScoresOut.split); marker check still applies — cost if wrong: none (schema has no data).

- Ruling: add CORS to API (origins http://127.0.0.1:3000, http://localhost:3000) in Task 8 fix round — spec requires web to reach API from browser — cost if wrong: trivial.

- Ruling: T9 buildRunConfig keeps optional defaults param — server normalizes settings so hash unaffected — cost if wrong: none for hashes.

- Task 10: complete (commits bb30168..d42e642, fix round 1). Ruling: stale failure rows under the 'Could not load failures' alert after a failed poll is acceptable (alert makes staleness explicit) — cost if wrong: one v-else-if.

- Ruling: T12 UNCHECKED banner shown only when paper rows are present — the banner qualifies paper numbers, which aren't displayed otherwise — cost if wrong: one condition.

## Accepted follow-ups

Final whole-branch review accepted the single-user deployment probe race, duplicate validation, and missing batching/stale-response/multi-page-fetch/mock-seed tests as follow-ups. Estimator cache-hit accounting remains an existing follow-up. The formerly parked filtered-sample issue was fixed.
