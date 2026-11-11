import type { Body, Fixture, Ok } from './types'
import { created, ok, page } from './types'
import { libraryDocuments } from './library'
import { adapters } from './adapters'
import { deployments } from './deployments'
import { allRuns, estimates, runCancelled, runLlm, runNew, runPaper, summary } from './runs'
import { clusterSummaries, membersByCluster } from './clusters'
import { allDocs, documentSummaries } from './documents'
import { compareDefault, compareRealistic, compareUnevaluated, scoresPaper } from './scores'
import { llmFailures } from './failures'
import { samples } from './enrichments'
import { articles as crawlArticles, crawls, document as crawlDocument, queued as queuedCrawl, sources as crawlSources, CRAWL_STORED } from './crawls'
import { CLUSTER_FLOOD, CLUSTER_SINGLE, CLUSTER_STRIKE, RUN_CANCELLED, RUN_LLM, RUN_NEW, RUN_PAPER } from './ids'

export type { Fixture } from './types'

/** Key used for both the fixture table and the OpenAPI operation list: `GET /runs/{run_id}`. */
export const routeKey = (method: string, path: string) => `${method.toUpperCase()} ${path}`

const run = (id: string) => ({ run_id: id })
const only = (id: string) => ({ query }: { query: URLSearchParams }) => query.getAll('run_id').every(r => r === id)

// Typed from the generated schema, so a response shape change in openapi.json breaks `pnpm typecheck`.
const runs: Ok<'/runs', 'get'>['items'] = allRuns.map(summary)
const clusterPage: Ok<'/runs/{run_id}/clusters', 'get'> = page(clusterSummaries)
const cluster = (id: string): Ok<'/runs/{run_id}/clusters/{cluster_id}', 'get'> => ({
  cluster: clusterSummaries.find(c => c.id === id)!,
  card_hash: id === CLUSTER_SINGLE ? null : `card-${id.slice(-2)}`
})
const membersFor = (id: string) => ok(page(membersByCluster[id]!), { cluster_id: id })
const estimateOk: Ok<'/runs/estimate', 'post'> = estimates.priced
const estimateNone: Ok<'/runs/estimate', 'post'> = estimates.unpriced
const validated: Ok<'/runs/validate', 'post'> = { config: runPaper.config, config_hash: runPaper.config_hash }
const createdOut: Ok<'/runs', 'post', 201> = { run_id: RUN_NEW, config_hash: runNew.config_hash, estimate: estimates.priced }
const startRequestShape: Body<'/runs', 'post'> = runPaper.config // keeps request bodies tied to the schema too
void startRequestShape

const backfill = { state: 'off', mode: 'off', from: null, until: null, per_hour: 60, started_at: null, finished_at: null, error: null, sitemaps: { total: 0, pending: 0, done: 0, skipped: 0, error: 0 }, urls: { found: 0, pending: 0, done: 0, stored: 0, existing: 0, rejected: 0, error: 0 }, oldest_reached: null, fetched_last_hour: 0, eta: 0 }
const site = { id: '00000000-0000-4000-8000-000000000201', name: 'example.com', domain: 'example.com', language: 'en', per_day: 3, feeds: [], sitemaps: [], enabled: true, check_every_minutes: 60, daily_cap: 50, min_interval_seconds: 2, max_parallel: 1, next_check_at: '2026-11-09T10:00:00Z', last_scheduled_at: null, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z', last_crawl: null, backfill_mode: 'off', backfill_from: null, backfill_per_hour: 60, backfill_state: 'off', backfill_progress: { done: 0, found: 0 } }

export const fixtures: Record<string, Fixture[]> = {
  'GET /library/documents': [ok(page(libraryDocuments.filter(d => d.version === d.versions)))],
  'GET /library/documents/{document_id}': libraryDocuments.map(d => ok(d, { document_id: d.id })),
  'GET /library/facets': [ok({ outlets: [], languages: [], changes: [{ value: 'edited', count: 1 }], total: 0 })],
  'POST /runs/{run_id}/rerun': [created(createdOut)],
  'GET /runs/{a}/snapshot-diff/{b}': [ok({ added: 2, removed: 1, new_versions: 3 })],
  'GET /health': [ok({ status: 'ok' })],
  'GET /adapters': [ok(adapters)],
  'GET /deployments': [ok(deployments)],
  'POST /deployments/probe': [ok(deployments)],

  'GET /sites': [ok(page([site]))],
  'POST /sites': [created(site)],
  'GET /sites/{site_id}': [ok(site)],
  'PATCH /sites/{site_id}': [ok(site)],
  'POST /sites/{site_id}/check': [{ status: 202, body: queuedCrawl }],
  'GET /sites/{site_id}/backfill': [ok(backfill)],
  'GET /sites/{site_id}/rechecks': [ok({ due_now: 2, scheduled: 14, done: 31, by_outcome: { unchanged: 40, edited: 3, gone: 1, redirected: 1, error: 0 }, last_checked_at: '2026-11-09T10:00:00Z' })],
  'POST /sites/{site_id}/backfill': [ok(backfill)],
  'GET /sites/{site_id}/crawls': [ok(page([queuedCrawl]))],
  'GET /crawls': [ok(page(crawls))],
  'POST /crawls': [{ status: 202, body: queuedCrawl }],
  'GET /crawls/{crawl_id}': crawls.map(c => ok(c, { crawl_id: c.id })),
  'GET /crawls/{crawl_id}/articles': crawls.map(c => ok(page(crawlArticles[c.id]!), { crawl_id: c.id })),
  'GET /crawls/{crawl_id}/sources': crawls.map(c => ok(page(crawlSources[c.id]!), { crawl_id: c.id })),
  'GET /crawls/{crawl_id}/documents/{document_id}': [ok(crawlDocument, { crawl_id: CRAWL_STORED, document_id: crawlDocument.id })],

  'GET /runs': [ok(page(runs))],
  'POST /runs': [created(createdOut)],
  'POST /runs/estimate': [
    // Selecting the not-ready local-qwen deployment yields an estimate with no amount.
    { ...ok(estimateNone), match: body => JSON.stringify(body).includes('local-qwen') },
    ok(estimateOk)
  ],
  'POST /runs/validate': [ok(validated)],

  'GET /runs/{run_id}': [runPaper, runLlm, runNew, runCancelled].map(r => ok(r, run(r.id))),
  'POST /runs/{run_id}/start': [ok({ ...runNew, state: 'running' as const, started_at: '2026-11-07T08:31:00Z' }, run(RUN_NEW))],
  'POST /runs/{run_id}/cancel': [ok({ ...runLlm, state: 'cancelled' as const, finished_at: '2026-11-06T15:20:00Z' }, run(RUN_LLM))],
  'POST /runs/{run_id}/resume': [ok({ ...runCancelled, state: 'running' as const, finished_at: null }, run(RUN_CANCELLED))],

  'GET /runs/{run_id}/clusters': [ok(clusterPage)],
  'GET /runs/{run_id}/clusters/{cluster_id}': [CLUSTER_FLOOD, CLUSTER_STRIKE, CLUSTER_SINGLE].map(id => ok(cluster(id), { cluster_id: id })),
  'GET /runs/{run_id}/clusters/{cluster_id}/members': [CLUSTER_FLOOD, CLUSTER_STRIKE, CLUSTER_SINGLE].map(membersFor),

  'GET /runs/{run_id}/documents': [ok(page(documentSummaries))],
  'GET /runs/{run_id}/documents/{document_id}': allDocs.map(d => ok(d, { document_id: d.id })),

  'GET /runs/{run_id}/enrichments/sample': [ok(page(samples))],
  'GET /runs/{run_id}/failures': [ok(page(llmFailures), run(RUN_LLM)), ok(page([]))],

  'GET /runs/{run_id}/scores': [ok(scoresPaper, run(RUN_PAPER))],
  'GET /compare': [
    { ...ok(compareUnevaluated), match: only(RUN_NEW) },
    { ...ok(compareRealistic), match: only(RUN_LLM) },
    ok(compareDefault)
  ]
}
