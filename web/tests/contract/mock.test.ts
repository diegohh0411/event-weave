import { beforeAll, describe, expect, it } from 'vitest'
import { createMockHandler } from '../../mock/server'

let call: (path: string, init?: RequestInit) => Promise<Response>
beforeAll(() => {
  const handler = createMockHandler()
  call = (path, init) => handler(new Request(`http://mock.test${path}`, init))
})

describe('mock server', () => {
  it('serves health', async () => {
    const r = await call('/health')
    expect(r.status).toBe(200)
  })

  it('pages with offset and limit', async () => {
    const r = await call('/runs?offset=1&limit=2')
    const body = await r.json()
    expect(body.offset).toBe(1)
    expect(body.limit).toBe(2)
    expect(body.items.length).toBeLessThanOrEqual(2)
    expect(body.total).toBeGreaterThan(2)
  })

  it('defaults to offset 0 and limit 50', async () => {
    const body = await (await call('/runs')).json()
    expect(body.offset).toBe(0)
    expect(body.limit).toBe(50)
  })

  it.each(['limit=201', 'limit=500', 'limit=0', 'offset=-1'])('rejects %s with 422', async (q) => {
    const r = await call(`/runs?${q}`)
    expect(r.status).toBe(422)
    expect((await r.json()).detail).toBeInstanceOf(Array)
  })

  it('accepts limit=200', async () => {
    expect((await call('/runs?limit=200')).status).toBe(200)
  })

  it('returns 404 for an unknown run', async () => {
    const r = await call('/runs/00000000-0000-0000-0000-000000000000')
    expect(r.status).toBe(404)
  })

  it('creates a run with 201', async () => {
    const r = await call('/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
    expect(r.status).toBe(201)
  })

  it('creates a queued crawl that can be refreshed and appears in history', async () => {
    const response = await call('/crawls', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sites: [{ domain: 'EXAMPLE.COM.', language: 'en' }] }) })
    expect(response.status).toBe(202)
    const crawl = await response.json()
    expect(crawl.status).toBe('queued')
    expect(crawl.request.sites[0]).toMatchObject({ domain: 'example.com', name: 'example.com', per_day: 3 })
    expect(await (await call(`/crawls/${crawl.id}`)).json()).toEqual(crawl)
    expect((await (await call('/crawls')).json()).items.some((c: any) => c.id === crawl.id)).toBe(true)
    expect((await (await call(`/crawls/${crawl.id}/articles`)).json()).total).toBe(0)
  })

  it('rejects invalid crawl requests without adding them to history', async () => {
    const before = (await (await call('/crawls')).json()).total
    for (const payload of [{ sites: [] }, { sites: [{ domain: '127.0.0.1', language: 'en' }] },
      { sites: [{ domain: 'example.com', language: 'en', feeds: ['https://user:pass@example.com/feed'] }] }]) {
      const r = await call('/crawls', { method: 'POST', body: JSON.stringify(payload) })
      expect(r.status).toBe(422)
    }
    expect((await (await call('/crawls')).json()).total).toBe(before)
  })

  it('enforces crawl membership and paging rules', async () => {
    expect((await call('/crawls/00000000-0000-0000-0000-000000000000/articles')).status).toBe(404)
    const history = await (await call('/crawls')).json()
    const crawl = history.items.find((c: any) => c.status === 'completed' && c.counters.stored > 0)
    const articles = await (await call(`/crawls/${crawl.id}/articles`)).json()
    const member = articles.items.find((a: any) => a.document_id)
    expect((await call(`/crawls/${crawl.id}/documents/${member.document_id}`)).status).toBe(200)
    expect((await call(`/crawls/${crawl.id}/documents/not-a-member`)).status).toBe(404)
    expect((await call(`/crawls/${crawl.id}/sources?limit=201`)).status).toBe(422)
  })

  it('answers CORS preflight so the browser can call it from the dev server', async () => {
    const r = await call('/runs', { method: 'OPTIONS', headers: { origin: 'http://127.0.0.1:3000', 'access-control-request-method': 'POST' } })
    expect(r.status).toBeLessThan(300)
    expect(r.headers.get('access-control-allow-origin')).toBeTruthy()
  })

  describe('enrichment sample', () => {
    const url = (q = '') => `/runs/0b8f6c1e-5d1a-4f3e-9a52-3c1d2e4f5a01/enrichments/sample${q}`

    it('reports total as the rows returned, not the fixture size', async () => {
      const body = await (await call(url('?limit=1'))).json()
      expect(body.items).toHaveLength(1)
      expect(body.total).toBe(1)
      expect(body.limit).toBe(1)
    })

    it('orders deterministically by seed and slices by offset', async () => {
      const all = (await (await call(url('?seed=3&limit=3'))).json()).items.map((i: any) => i.id)
      expect(all).toHaveLength(3)
      expect((await (await call(url('?seed=3&limit=3'))).json()).items.map((i: any) => i.id)).toEqual(all)
      const tail = (await (await call(url('?seed=3&offset=1&limit=3'))).json()).items.map((i: any) => i.id)
      expect(tail).toEqual(all.slice(1))
    })

    it('filters by enricher', async () => {
      const none = await (await call(url('?enricher=nope'))).json()
      expect(none).toMatchObject({ items: [], total: 0 })
      const some = await (await call(url('?enricher=clairic'))).json()
      expect(some.items.every((i: any) => i.enricher === 'clairic')).toBe(true)
    })
  })
})
