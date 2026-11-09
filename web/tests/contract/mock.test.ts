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
