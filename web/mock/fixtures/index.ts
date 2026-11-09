import type { Body, Fixture, Ok } from './types'
import { created, ok, page } from './types'
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

export const fixtures: Record<string, Fixture[]> = {
  'GET /health': [ok({ status: 'ok' })],
  'GET /adapters': [ok(adapters)],
  'GET /deployments': [ok(deployments)],
  'POST /deployments/probe': [ok(deployments)],

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
