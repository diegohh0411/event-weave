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
})
