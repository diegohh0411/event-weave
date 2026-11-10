/**
 * Mock of the weave API for developing the web app with no backend running.
 *
 * Serves the typed fixtures in ./fixtures for every route in openapi.json. List routes page the
 * fixture arrays with the API's rules: offset >= 0 (default 0), limit 1..200 (default 50), and a
 * 422 in FastAPI's error shape otherwise. Unknown ids give 404; run actions the fixtures do not
 * cover give 409 like the API's RunStateError.
 */
import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { pathToFileURL } from 'node:url'
import {
  createApp, eventHandler, getRequestURL, readRawBody, setResponseHeaders, setResponseStatus, toNodeListener, toWebHandler
} from 'h3'
import { fixtures, type Fixture } from './fixtures'
import { emptyCounters } from './fixtures/crawls'
import type { Schemas } from './fixtures/types'

const MAX_LIMIT = 200
const DEFAULT_LIMIT = 50

interface Route { method: string, template: string, regex: RegExp, names: string[], fixtures: Fixture[] }

const routes: Route[] = Object.entries(fixtures).map(([key, list]) => {
  const [method, template] = key.split(' ') as [string, string]
  const names: string[] = []
  const regex = new RegExp('^' + template.replace(/\{(\w+)\}/g, (_, n: string) => { names.push(n); return '([^/]+)' }) + '/?$')
  return { method, template, regex, names, fixtures: list }
}).sort((a, b) => a.names.length - b.names.length) // static routes win over parameterised ones

const knownRunIds = new Set(
  (fixtures['GET /runs/{run_id}'] ?? []).map(f => f.params?.run_id).filter((v): v is string => !!v)
)

type Row = Record<string, unknown>

// Small stable string hash (FNV-1a) so the seeded sample order is deterministic.
function seedHash(text: string) {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return h >>> 0
}
// Query filters the real routes support, applied to the fixture rows before paging.
const filters: Record<string, (item: Row, q: URLSearchParams) => boolean> = {
  'GET /library/documents': (i, q) => (!q.getAll('outlets').length || q.getAll('outlets').includes(String(i.outlet)))
    && (!q.getAll('languages').length || q.getAll('languages').includes(String(i.language)))
    && (!q.get('search') || String(i.title).toLowerCase().includes(q.get('search')!.toLowerCase()))
    && (!q.get('published_from') || String(i.published_at ?? '') >= q.get('published_from')!)
    && (!q.get('published_to') || (!!i.published_at && String(i.published_at) < q.get('published_to')!)),
  'GET /adapters': (i, q) => !q.get('stage') || i.stage === q.get('stage'),
  'GET /deployments': (i, q) => (!q.get('capability') || i.capability === q.get('capability'))
    && (!q.get('ready') || String(i.ready) === q.get('ready')),
  'GET /runs/{run_id}/failures': (i, q) => !q.get('stage') || i.stage === q.get('stage'),
  'GET /runs/{run_id}/documents': (i, q) => (!q.get('language') || i.language === q.get('language'))
    && (!q.get('q') || String(i.title).toLowerCase().includes(q.get('q')!.toLowerCase())),
  'GET /runs/{run_id}/clusters': (i, q) => (!q.get('language') || (i.languages as string[]).includes(q.get('language')!))
    && (!q.get('min_size') || (i.size as number) >= Number(q.get('min_size')))
    && (!q.get('max_size') || (i.size as number) <= Number(q.get('max_size')))
    && (!q.get('q') || JSON.stringify(i.card ?? '').toLowerCase().includes(q.get('q')!.toLowerCase()))
}

const validation = (loc: string, msg: string, type: string, input: unknown) => ({ detail: [{ type, loc: ['query', loc], msg, input }] })

function pagingError(q: URLSearchParams) {
  const offset = q.get('offset')
  if (offset !== null && !(/^\d+$/.test(offset))) {
    return validation('offset', 'Input should be greater than or equal to 0', 'greater_than_equal', offset)
  }
  const limit = q.get('limit')
  if (limit !== null && !(/^\d+$/.test(limit) && Number(limit) >= 1 && Number(limit) <= MAX_LIMIT)) {
    return validation('limit', `Input should be between 1 and ${MAX_LIMIT}`, 'less_than_equal', limit)
  }
  return null
}

function pick(route: Route, params: Record<string, string>, query: URLSearchParams, body: unknown): Fixture | undefined {
  const fits = (f: Fixture) => {
    const paramsOk = Object.entries(f.params ?? {}).every(([k, v]) => params[k] === v || query.getAll(k).includes(v))
    return paramsOk && (f.match?.({ body, query }) ?? true)
  }
  // Fixtures that narrow by params or match() come before the route's default.
  const narrowed = route.fixtures.filter(f => f.params || f.match)
  const defaults = route.fixtures.filter(f => !f.params && !f.match)
  return [...narrowed, ...defaults].find(fits)
}

export interface MockResponse { status: number, body: unknown }

// The mock persists new queued jobs for this server instance; it never executes crawls.
function normalizeCrawl(value: unknown): Schemas['CrawlRequest'] | null {
  const input = value as Record<string, any> | null
  if (!input || !Array.isArray(input.sites) || input.sites.length < 1 || input.sites.length > 20) return null
  const days = input.days ?? 1
  if (!Number.isInteger(days) || days < 1 || days > 30) return null
  const validDomain = (s: string) => s.length <= 253 && s.includes('.') && !/^\d+$/.test(s.split('.').at(-1)!) &&
    s.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
  const seen = new Set<string>()
  const sites: Schemas['CrawlSite'][] = []
  for (const site of input.sites) {
    if (!site || typeof site.domain !== 'string') return null
    const domain = site.domain.trim().toLowerCase().replace(/\.+$/, '')
    if (!validDomain(domain) || seen.has(domain) || !['en', 'es', 'hi', 'tr', 'uk'].includes(site.language)) return null
    seen.add(domain)
    const per_day = site.per_day ?? 3
    if (!Number.isInteger(per_day) || per_day < 1 || per_day > 200 || (site.name != null && typeof site.name !== 'string')) return null
    const validSources = (urls: unknown) => Array.isArray(urls) && urls.length <= 20 && urls.every((s: unknown) => {
      if (typeof s !== 'string' || /\s/.test(s)) return false
      try {
        const u = new URL(s)
        return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password && !u.port && validDomain(u.hostname)
      } catch { return false }
    })
    const feeds = site.feeds ?? [], sitemaps = site.sitemaps ?? []
    if (!validSources(feeds) || !validSources(sitemaps)) return null
    sites.push({ domain, name: site.name ?? domain, language: site.language, per_day, feeds, sitemaps })
  }
  return { sites, days }
}

type CrawlState = Schemas['CrawlOut'][]
export function respond(method: string, pathname: string, query: URLSearchParams, rawBody: string | undefined, createdCrawls: CrawlState = []): MockResponse {
  if (pathname === '/openapi.json') return { status: 200, body: { note: 'see web/openapi.json' } }
  for (const route of routes) {
    const m = route.method === method ? route.regex.exec(pathname) : null
    if (!m) continue
    const params = Object.fromEntries(route.names.map((n, i) => [n, decodeURIComponent(m[i + 1]!)]))
    if (params.run_id && !knownRunIds.has(params.run_id)) return { status: 404, body: { detail: `run ${params.run_id} not found` } }
    let body: unknown
    try { body = rawBody ? JSON.parse(rawBody) : undefined } catch { return { status: 422, body: { detail: [{ type: 'json_invalid', loc: ['body'], msg: 'JSON decode error' }] } } }
    if (route.template === '/crawls' && method === 'POST') {
      const request = normalizeCrawl(body)
      if (!request) return { status: 422, body: { detail: [{ type: 'value_error', loc: ['body'], msg: 'Invalid crawl request', input: body }] } }
      const crawl: Schemas['CrawlOut'] = { id: randomUUID(), request, status: 'queued', created_at: new Date().toISOString(),
        started_at: null, finished_at: null, counters: emptyCounters(), error: null }
      createdCrawls.unshift(crawl)
      return { status: 202, body: crawl }
    }
    const createdCrawl = params.crawl_id && createdCrawls.find(c => c.id === params.crawl_id)
    if (createdCrawl) {
      if (route.template === '/crawls/{crawl_id}') return { status: 200, body: createdCrawl }
      if (route.template === '/crawls/{crawl_id}/articles' || route.template === '/crawls/{crawl_id}/sources') {
        const bad = pagingError(query)
        if (bad) return { status: 422, body: bad }
        return { status: 200, body: { items: [], total: 0, offset: Number(query.get('offset') ?? 0), limit: Number(query.get('limit') ?? DEFAULT_LIMIT) } }
      }
    }
    if (route.template === '/compare' && query.getAll('run_id').length === 0) {
      return { status: 422, body: validation('run_id', 'Field required', 'missing', null) }
    }
    if (route.template === '/library/facets') {
      const docs = (fixtures['GET /library/documents']![0]!.body as { items: Row[] }).items.filter(i => filters['GET /library/documents']!(i, query))
      const counts = (key: string) => [...new Set(docs.map(d => String(d[key])))].sort().map(value => ({ value, count: docs.filter(d => d[key] === value).length }))
      return { status: 200, body: { outlets: counts('outlet'), languages: counts('language'), total: docs.length } }
    }
    const fixture = pick(route, params, query, body)
    if (!fixture) {
      return method === 'POST'
        ? { status: 409, body: { detail: `run ${params.run_id} cannot do that in its current state` } }
        : { status: 404, body: { detail: `${route.template}: nothing for these parameters` } }
    }
    if (route.template === '/runs' && method === 'POST' && JSON.stringify(body ?? {}).includes('local-qwen')) {
      // The API's ConfigError shape: a deployment that is not ready.
      return { status: 422, body: { problems: ['deployment local-qwen (llm) is not ready: connection refused at http://127.0.0.1:11434'] } }
    }
    const data = fixture.body as { items?: Row[] } | undefined
    if (data && Array.isArray(data.items)) {
      const bad = pagingError(query)
      if (bad) return { status: 422, body: bad }
      const offset = Number(query.get('offset') ?? 0)
      const limit = Number(query.get('limit') ?? DEFAULT_LIMIT)
      if (route.template === '/runs/{run_id}/enrichments/sample') {
        // Like the API: `total` counts the rows returned after filtering, seeded ordering and slicing.
        const seed = query.get('seed') ?? '0'
        const wanted = query.get('enricher')
        const ordered = data.items
          .filter(i => !wanted || i.enricher === wanted)
          .map(i => [seedHash(`${seed}:${i.id}`), i] as const)
          .sort((a, b) => a[0] - b[0] || String(a[1].id).localeCompare(String(b[1].id)))
          .map(([, i]) => i)
        const items = ordered.slice(offset, offset + limit)
        return { status: fixture.status, body: { items, total: items.length, offset, limit } }
      }
      const keep = filters[`${method} ${route.template}`]
      const rows = keep ? data.items.filter(i => keep(i, query)) : data.items
      const allRows = route.template === '/crawls' ? [...createdCrawls, ...rows] : rows
      return { status: fixture.status, body: { items: allRows.slice(offset, offset + limit), total: allRows.length, offset, limit } }
    }
    return { status: fixture.status, body: fixture.body }
  }
  return { status: 404, body: { detail: 'Not Found' } }
}

const cors = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, POST, OPTIONS',
  'access-control-allow-headers': '*'
}

export function createMockApp() {
  const app = createApp()
  const createdCrawls: CrawlState = []
  app.use(eventHandler(async (event) => {
    setResponseHeaders(event, cors)
    if (event.method === 'OPTIONS') {
      setResponseStatus(event, 204)
      return ''
    }
    const url = getRequestURL(event)
    const raw = event.method === 'POST' ? await readRawBody(event, 'utf8') : undefined
    const out = respond(event.method, url.pathname, url.searchParams, raw, createdCrawls)
    setResponseStatus(event, out.status)
    return out.body
  }))
  return app
}

/** A fetch-style handler over the mock, for tests and anything else that has no socket. */
export const createMockHandler = () => toWebHandler(createMockApp())

export function listen(port = Number(process.env.MOCK_PORT ?? 4010), host = '127.0.0.1') {
  const server = createServer(toNodeListener(createMockApp()))
  server.listen(port, host, () => console.log(`mock API on http://${host}:${port}`))
  return server
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) listen()
