import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import RunsList from '../../app/pages/runs/index.vue'
import RunPage from '../../app/pages/runs/[id].vue'
import { createMockHandler } from '../../mock/server'
import { runCancelled, runLlm, runNew, runPaper } from '../../mock/fixtures/runs'

const { add } = vi.hoisted(() => ({ add: vi.fn() }))
mockNuxtImport('useToast', () => () => ({ add }))

type Override = (req: Request) => Response | undefined
let overrides: Override[] = []
let calls: string[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

beforeEach(() => {
  overrides = []
  calls = []
  add.mockReset()
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    calls.push(`${req.method} ${new URL(req.url).pathname}`)
    for (const o of overrides) {
      const r = o(req.clone())
      if (r) return r
    }
    return handler(req)
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const mountRun = async (id: string) => {
  const w = await mountSuspended(RunPage, { route: `/runs/${id}` })
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}
const labels = (w: Awaited<ReturnType<typeof mountRun>>) =>
  w.findAll('button').map(b => b.text().trim()).filter(t => ['Start', 'Cancel', 'Resume'].includes(t))

describe('runs list', () => {
  it('lists every run with a link to it', async () => {
    const w = await mountSuspended(RunsList)
    await vi.waitFor(() => expect(w.text()).toContain('paper-baseline'))
    for (const r of [runPaper, runLlm, runNew, runCancelled]) expect(w.text()).toContain(r.name)
    expect(w.find(`a[href="/runs/${runLlm.id}"]`).exists()).toBe(true)
  })
})

describe('run page', () => {
  it('shows_only_valid_action', async () => {
    expect(labels(await mountRun(runNew.id))).toEqual(['Start'])
    expect(labels(await mountRun(runLlm.id))).toEqual(['Cancel'])
    expect(labels(await mountRun(runCancelled.id))).toEqual(['Resume'])
    expect(labels(await mountRun(runPaper.id))).toEqual([])
  })

  it('shows stage progress, dollars and mentions per hour', async () => {
    const w = await mountRun(runLlm.id)
    expect(w.text()).toContain('cluster')
    expect(w.text()).toContain('3100 queued')
    expect(w.text()).toContain('6 failed')
    expect(w.text()).toContain('$3.4172')
    expect(w.text()).toContain('estimated')
    expect(w.text()).toContain('912')
    expect(w.findAll('[role=progressbar]').length).toBeGreaterThan(3)
    const p = await mountRun(runPaper.id)
    expect(p.text()).not.toContain('estimated')
    const n = await mountRun(runNew.id)
    expect(n.text()).toContain('—')
  })

  it('failed_item_shows_error', async () => {
    const w = await mountRun(runLlm.id)
    await vi.waitFor(() => expect(w.text()).toContain('context window exceeded: 9120 > 8192 tokens'))
    expect(w.text()).toContain('deployment claude-sonnet returned 529 overloaded')
    expect(calls.some(c => c.endsWith('/failures'))).toBe(true)
  })

  it('links to clusters, spot check and scores', async () => {
    const w = await mountRun(runPaper.id)
    for (const p of ['clusters', 'spot-check']) expect(w.find(`a[href="/runs/${runPaper.id}/${p}"]`).exists()).toBe(true)
    const scores = w.find(`a[href="/compare?run_id=${runPaper.id}"]`)
    expect(scores.exists()).toBe(true)
    expect(scores.text()).toBe('Scores')
  })

  it('shows the detail of a 409 in a toast', async () => {
    overrides.push(r => r.method === 'POST' ? json({ detail: 'run is already running' }, 409) : undefined)
    const w = await mountRun(runNew.id)
    await w.findAll('button').find(b => b.text().includes('Start'))!.trigger('click')
    await vi.waitFor(() => expect(add).toHaveBeenCalled())
    expect(add.mock.calls[0]![0].description).toBe('run is already running')
  })

  it('sends one request for a double click', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => (release = r))
    const handler = createMockHandler()
    vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
      const req = input instanceof Request ? input : new Request(input, init)
      calls.push(`${req.method} ${new URL(req.url).pathname}`)
      if (req.method === 'POST') await gate
      return handler(req)
    })
    const w = await mountRun(runNew.id)
    const b = w.findAll('button').find(x => x.text().includes('Start'))!
    await b.trigger('click')
    await b.trigger('click')
    release()
    await flushPromises()
    expect(calls.filter(c => c.startsWith('POST')).length).toBe(1)
  })

  it('polling_stops_when_done', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const w = await mountRun(runLlm.id)
    const gets = () => calls.filter(c => c === `GET /runs/${runLlm.id}`).length
    const before = gets()
    await vi.advanceTimersByTimeAsync(5000)
    expect(gets()).toBe(before + 1)
    overrides.push(r => new URL(r.url).pathname === `/runs/${runLlm.id}` ? json({ ...runLlm, state: 'done' }) : undefined)
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    const after = gets()
    expect(after).toBe(before + 2)
    expect(labels(w)).toEqual([])
    await vi.advanceTimersByTimeAsync(20000)
    expect(gets()).toBe(after)
  })

  it('refreshes the failures page on each poll', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    await mountRun(runLlm.id)
    const fails = () => calls.filter(c => c.endsWith('/failures')).length
    const before = fails()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fails()).toBe(before + 1)
  })

  it('shows an error, not "No failed jobs", when failures cannot load', async () => {
    overrides.push(r => new URL(r.url).pathname.endsWith('/failures') ? json({ detail: 'database is down' }, 500) : undefined)
    const w = await mountRun(runLlm.id)
    await vi.waitFor(() => expect(w.text()).toContain('Could not load failures: database is down'))
    expect(w.text()).not.toContain('No failed jobs')
  })

  it('shows an error when the failures request throws', async () => {
    overrides.push((r) => { if (new URL(r.url).pathname.endsWith('/failures')) throw new Error('network down'); return undefined })
    const w = await mountRun(runLlm.id)
    await vi.waitFor(() => expect(w.text()).toContain('Could not load failures: network down'))
    expect(w.text()).not.toContain('No failed jobs')
  })

  it('stops polling on unmount', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const w = await mountRun(runLlm.id)
    w.unmount()
    const before = calls.length
    await vi.advanceTimersByTimeAsync(20000)
    expect(calls.length).toBe(before)
  })
})
