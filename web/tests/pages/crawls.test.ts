import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { createMockHandler } from '../../mock/server'
import { CRAWL_PAYWALLED, CRAWL_QUEUED, CRAWL_STORED, document, queued, stored } from '../../mock/fixtures/crawls'

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }))
mockNuxtImport('navigateTo', () => navigateTo)
let calls: Request[] = []
let override: (req: Request) => Promise<Response | undefined> = async () => undefined
const mounted: { unmount: () => void }[] = []
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } })
const track = <T extends { unmount: () => void }>(w: T) => { mounted.push(w); return w }
const btn = (w: any, label: string) => w.findAll('button').find((b: any) => b.text().includes(label))!
beforeEach(() => {
  calls = []; override = async () => undefined; navigateTo.mockReset()
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    calls.push(req.clone())
    return await override(req.clone()) ?? handler(req)
  })
})
afterEach(() => { mounted.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); vi.unstubAllGlobals() })

async function list() { const { default: Page } = await import('../../app/pages/crawls/index.vue'); return track(await mountSuspended(Page)) }
async function detail(id: string) { const { default: Page } = await import('../../app/pages/crawls/[id].vue'); return track(await mountSuspended(Page, { route: `/crawls/${id}` })) }
async function doc(id: string, did: string) { const { default: Page } = await import('../../app/pages/crawls/[id]/documents/[did].vue'); return track(await mountSuspended(Page, { route: `/crawls/${id}/documents/${did}` })) }

describe('crawl pages', () => {
  it('submits multiple sites, languages and advanced sources and opens the new crawl', async () => {
    const w = await list()
    await w.find('[name="domain-0"]').setValue('example.com')
    await w.find('[name="language-0"]').setValue('es')
    await w.find('[name="per-day-0"]').setValue('4')
    await w.find('[name="feeds-0"]').setValue('https://example.com/rss\nhttps://example.com/feed')
    await w.find('[name="sitemaps-0"]').setValue('https://example.com/sitemap.xml')
    await btn(w, 'Add site').trigger('click')
    await w.find('[name="domain-1"]').setValue('other.com')
    await w.find('[name="language-1"]').setValue('tr')
    await w.find('[name="days"]').setValue('7')
    await w.find('form').trigger('submit')
    await vi.waitFor(() => expect(navigateTo).toHaveBeenCalled())
    const req = calls.find(c => c.method === 'POST' && new URL(c.url).pathname === '/crawls')!
    expect(await req.json()).toEqual({ days: 7, sites: [
      { domain: 'example.com', language: 'es', per_day: 4, feeds: ['https://example.com/rss', 'https://example.com/feed'], sitemaps: ['https://example.com/sitemap.xml'] },
      { domain: 'other.com', language: 'tr', per_day: 3, feeds: [], sitemaps: [] }
    ] })
    expect(navigateTo.mock.calls[0]![0]).toMatch(/^\/crawls\//)
  })
  it('shows API validation errors without navigating', async () => {
    override = async r => r.method === 'POST' ? json({ detail: [{ loc: ['body', 'sites', 0, 'domain'], msg: 'Invalid domain' }] }, 422) : undefined
    const w = await list(); await w.find('[name="domain-0"]').setValue('bad'); await w.find('form').trigger('submit')
    await vi.waitFor(() => expect(w.text()).toContain('Invalid domain'))
    expect(navigateTo).not.toHaveBeenCalled()
  })
  it('loads a crawl by route ID and explains four paywalls and source errors', async () => {
    override = async r => new URL(r.url).pathname === `/crawls/${CRAWL_PAYWALLED}` ? json({ ...stored, id: CRAWL_PAYWALLED, counters: { ...stored.counters, fetched: 4, stored: 0, existing: 0, rejected: 4, reasons: { paywall: 4 } } }) : undefined
    const w = await detail(CRAWL_PAYWALLED)
    await vi.waitFor(() => expect(w.text()).toContain('4 articles skipped'))
    expect(w.text()).toContain('paywall'); expect(w.text()).toContain('Discovery failed')
    expect(w.text()).toContain('0 stored'); expect(w.text()).toContain('4 fetched')
  })
  it('shows the saved lookback and per-day limit on crawl details', async () => {
    override = async r => new URL(r.url).pathname === `/crawls/${CRAWL_STORED}`
      ? json({ ...stored, request: { ...stored.request, days: 7, sites: [{ ...stored.request.sites[0], per_day: 42 }] } })
      : undefined
    const w = await detail(CRAWL_STORED)
    await vi.waitFor(() => expect(w.text()).toContain('Lookback: 7 days'))
    expect(w.text()).toContain('example.com — maximum 42 articles per day')
  })
  it('distinguishes robots-blocked discovery from a successful empty window', async () => {
    override = async r => {
      const path = new URL(r.url).pathname
      if (path === `/crawls/${CRAWL_PAYWALLED}`) return json({ ...stored, id: CRAWL_PAYWALLED, counters: { ...queued.counters } })
      if (path.endsWith('/sources')) return json({ items: [{ site: 'example.com', url: 'https://example.com/feed', kind: 'feed', status: 'error', error: 'robots disallowed' }], total: 1, offset: 0, limit: 200 })
      return undefined
    }
    const w = await detail(CRAWL_PAYWALLED)
    await vi.waitFor(() => expect(w.text()).toContain('robots disallowed'))
    expect(w.text()).toContain('failed discovery')
    expect(w.text()).not.toContain('No articles found in this time window')
    override = async r => {
      const path = new URL(r.url).pathname
      if (path === `/crawls/${CRAWL_PAYWALLED}`) return json({ ...stored, id: CRAWL_PAYWALLED, counters: { ...queued.counters } })
      if (path.endsWith('/sources')) return json({ items: [{ site: 'example.com', url: 'https://example.com/feed', kind: 'feed', status: 'completed', error: null }], total: 1, offset: 0, limit: 200 })
      return undefined
    }
    const other = await detail(CRAWL_PAYWALLED)
    await vi.waitFor(() => expect(other.text()).toContain('No articles found in the 1-day lookback window'))
  })
  it('does not mistake missing or still-loading sources for an empty time window', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => (release = r))
    override = async r => {
      const path = new URL(r.url).pathname
      if (path === `/crawls/${CRAWL_PAYWALLED}`) return json({ ...stored, id: CRAWL_PAYWALLED, counters: { ...queued.counters } })
      if (path.endsWith('/sources')) { await gate; return json({ items: [], total: 0, offset: 0, limit: 200 }) }
      return undefined
    }
    const w = await detail(CRAWL_PAYWALLED)
    await vi.waitFor(() => expect(w.text()).toContain('completed'))
    expect(w.text()).not.toContain('No articles found in this time window')
    release()
    await vi.waitFor(() => expect(w.text()).toContain('No sources recorded'))
    expect(w.text()).toContain('explicit feed'); expect(w.text()).not.toContain('No articles found in this time window')
  })
  it('ignores an old crawl response after the route changes', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => (release = r))
    override = async r => {
      if (new URL(r.url).pathname === `/crawls/${CRAWL_QUEUED}`) { await gate; return json(queued) }
      return undefined
    }
    const w = await detail(CRAWL_QUEUED)
    await useRouter().push(`/crawls/${CRAWL_STORED}`)
    await vi.waitFor(() => expect(w.text()).toContain(CRAWL_STORED))
    release(); await flushPromises()
    expect(w.text()).toContain(CRAWL_STORED); expect(w.text()).not.toContain(CRAWL_QUEUED)
  })
  it('links stored and existing documents and renders escaped body without run endpoints', async () => {
    const w = await detail(CRAWL_STORED)
    await vi.waitFor(() => expect(w.find(`a[href="/crawls/${CRAWL_STORED}/documents/${document.id}"]`).exists()).toBe(true))
    expect(w.find(`a[href="/?crawl_id=${CRAWL_STORED}"]`).exists()).toBe(true)
    override = async r => new URL(r.url).pathname.endsWith(`/documents/${document.id}`) ? json({ ...document, text: '<script>alert(1)</script>' }) : undefined
    const d = await doc(CRAWL_STORED, document.id)
    await vi.waitFor(() => expect(d.text()).toContain('<script>alert(1)</script>'))
    expect(d.find('script').exists()).toBe(false); expect(d.text()).toContain(document.title); expect(d.text()).toContain(document.url)
    expect(calls.some(c => new URL(c.url).pathname.startsWith('/runs/'))).toBe(false)
  })
  it('polls active jobs every two seconds, stops on completion and unmount', async () => {
    const w = await detail(CRAWL_QUEUED); await flushPromises()
    const count = () => calls.filter(c => new URL(c.url).pathname === `/crawls/${CRAWL_QUEUED}`).length
    expect(count()).toBe(1)
    override = async r => new URL(r.url).pathname === `/crawls/${CRAWL_QUEUED}` ? json({ ...queued, status: 'completed' }) : undefined
    await vi.waitFor(() => expect(count()).toBe(2), { timeout: 3500 })
    await vi.waitFor(() => expect(w.text()).toContain('completed'))
    await new Promise(r => setTimeout(r, 2100)); expect(count()).toBe(2)
    w.unmount()
  }, 7000)
  it('allows retrying a failed crawl detail request', async () => {
    let failed = true
    override = async r => new URL(r.url).pathname === `/crawls/${CRAWL_QUEUED}` && failed ? json({ detail: 'database unavailable' }, 503) : undefined
    const w = await detail(CRAWL_QUEUED)
    await vi.waitFor(() => expect(w.text()).toContain('database unavailable'))
    failed = false
    await btn(w, 'Retry').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('queued'))
  })
  it('stops an active poll on unmount', async () => {
    const w = await detail(CRAWL_QUEUED); await flushPromises(); w.unmount()
    const before = calls.length; await new Promise(r => setTimeout(r, 2100)); expect(calls.length).toBe(before)
  }, 5000)
})
