import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import ClustersPage from '../../app/pages/runs/[id]/clusters/index.vue'
import ClusterPage from '../../app/pages/runs/[id]/clusters/[cid].vue'
import { createMockHandler } from '../../mock/server'
import { CLUSTER_FLOOD, CLUSTER_STRIKE, DOC_EN, RUN_LLM } from '../../mock/fixtures/ids'

type Override = (req: Request) => Response | Promise<Response> | undefined
let overrides: Override[] = []
let calls: URL[] = []
const mounted: { unmount: () => void }[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

beforeEach(() => {
  overrides = []
  calls = []
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    calls.push(new URL(req.url))
    for (const o of overrides) {
      const r = o(req.clone())
      if (r) return r
    }
    return handler(req)
  })
})
afterEach(() => {
  mounted.splice(0).forEach(w => w.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const listCalls = () => calls.filter(u => u.pathname === `/runs/${RUN_LLM}/clusters`)
const mountList = async (query = '') => {
  const w = await mountSuspended(ClustersPage, { route: `/runs/${RUN_LLM}/clusters${query}` })
  mounted.push(w)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}
const mountCluster = async (cid: string) => {
  const w = await mountSuspended(ClusterPage, { route: `/runs/${RUN_LLM}/clusters/${cid}` })
  mounted.push(w)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}

describe('clusters browser', () => {
  it('filters_round_trip_to_query', async () => {
    const w = await mountList('?language=en&min_size=2&max_size=5&event_type=labor_action&min_significance=0.5&sort=recent&q=dock&page=1')
    const last = listCalls().at(-1)!.searchParams
    expect(Object.fromEntries(last)).toMatchObject({
      language: 'en', min_size: '2', max_size: '5', event_type: 'labor_action', min_significance: '0.5', sort: 'recent', q: 'dock'
    })
    expect((w.find('[data-test=language]').element as HTMLSelectElement).value).toBe('en')
    expect((w.find('[data-test=sort]').element as HTMLSelectElement).value).toBe('recent')
    expect((w.find('input[data-test=min-size]').element as HTMLInputElement).value).toBe('2')
    expect((w.find('input[data-test=search]').element as HTMLInputElement).value).toBe('dock')

    // Changing a filter writes it to the URL and reloads.
    await w.find('[data-test=language]').setValue('hi')
    await vi.waitFor(() => expect(listCalls().at(-1)!.searchParams.get('language')).toBe('hi'))
    expect(useRoute().query.language).toBe('hi')
    expect(useRoute().query.sort).toBe('recent')
  })

  it('debounces the search box by 300 ms and writes q to the URL', async () => {
    const w = await mountList()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const before = listCalls().length
    await w.find('input[data-test=search]').setValue('flo')
    await w.find('input[data-test=search]').setValue('flood')
    await vi.advanceTimersByTimeAsync(299)
    expect(listCalls().length).toBe(before)
    await vi.advanceTimersByTimeAsync(2)
    await vi.advanceTimersByTimeAsync(50)
    expect(listCalls().length).toBe(before + 1)
    expect(listCalls().at(-1)!.searchParams.get('q')).toBe('flood')
    expect(useRoute().query.q).toBe('flood')
  })

  it('lists clusters with links and card fields', async () => {
    const w = await mountList()
    expect(w.find(`a[href="/runs/${RUN_LLM}/clusters/${CLUSTER_FLOOD}"]`).exists()).toBe(true)
    expect(w.text()).toContain('flooded')
    expect(w.text()).toContain('Szeged, Hungary')
  })

  it('shows an error alert when the load fails', async () => {
    overrides.push(r => new URL(r.url).pathname.endsWith('/clusters') ? json({ detail: 'database is down' }, 500) : undefined)
    const w = await mountList()
    expect(w.text()).toContain('Could not load clusters')
    expect(w.text()).not.toContain('No clusters')
  })

  it('ignores a response that a newer filter change replaced', async () => {
    let releaseSlow!: () => void
    const slow = new Promise<void>(r => (releaseSlow = r))
    const handler = createMockHandler()
    const w = await mountList()
    overrides.push((r) => {
      const u = new URL(r.url)
      if (u.pathname.endsWith('/clusters') && u.searchParams.get('language') === 'es') {
        return slow.then(() => handler(new Request(r.url)))
      }
      return undefined
    })
    await w.find('[data-test=language]').setValue('es')
    await w.find('[data-test=language]').setValue('hi')
    await vi.waitFor(() => expect(listCalls().filter(u => u.searchParams.get('language') === 'hi').length).toBe(1))
    await flushPromises()
    releaseSlow()
    await flushPromises()
    expect(w.text()).not.toContain('Szeged, Hungary')
    expect(w.findAll(`a[href^="/runs/${RUN_LLM}/clusters/c1"]`).length).toBe(1)
  })
})

describe('cluster page', () => {
  it('every_member_row_has_decision_table', async () => {
    const w = await mountCluster(CLUSTER_FLOOD)
    const rows = w.findAll('[data-test=member]')
    expect(rows.length).toBe(3)
    for (const r of rows) expect(r.find('[data-test=decision]').exists()).toBe(true)
    // The seeding member has no decision recorded.
    expect(rows[0]!.text()).toContain('No decision recorded')
    expect(rows[1]!.text()).toContain('Same flood, same morning.')
    expect(rows[1]!.text()).toContain('join')
    expect(rows[1]!.text()).toContain('0.93')
    expect(rows[1]!.find(`a[href="/runs/${RUN_LLM}/clusters/${CLUSTER_STRIKE}"]`).exists()).toBe(true)
    expect(rows[1]!.find('[data-test=chosen]').exists()).toBe(true)
  })

  it('shows the card, the highlighted trigger and the document link', async () => {
    const w = await mountCluster(CLUSTER_FLOOD)
    for (const t of ['flooded', 'River Tisza', 'Szeged, Hungary', '2026-03-14']) expect(w.text()).toContain(t)
    const first = w.find('[data-test=member]')
    expect(first.findAll('mark').map(m => m.text())).toEqual(['flooded'])
    expect(first.find(`a[href="/runs/${RUN_LLM}/documents/${DOC_EN}"]`).exists()).toBe(true)
  })

  it('pages the members', async () => {
    const w = await mountCluster(CLUSTER_FLOOD)
    const m = calls.filter(u => u.pathname.endsWith('/members')).at(-1)!
    expect(m.searchParams.get('limit')).toBe('20')
    expect(m.searchParams.get('offset')).toBe('0')
    expect(w.exists()).toBe(true)
  })

  it('shows an error alert when the members cannot load', async () => {
    overrides.push(r => new URL(r.url).pathname.endsWith('/members') ? json({ detail: 'boom' }, 500) : undefined)
    const w = await mountCluster(CLUSTER_FLOOD)
    expect(w.text()).toContain('Could not load members')
  })

  it('shows an error alert when the cluster cannot load', async () => {
    overrides.push(r => new URL(r.url).pathname.endsWith(CLUSTER_FLOOD) ? json({ detail: 'nope' }, 404) : undefined)
    const w = await mountCluster(CLUSTER_FLOOD)
    expect(w.text()).toContain('Could not load the cluster')
  })
})

describe('HighlightedText component', () => {
  it('renders marks from text slices', async () => {
    const { default: HighlightedText } = await import('../../app/components/HighlightedText.vue')
    const w = await mountSuspended(HighlightedText, { props: { text: '<b>x</b> hit', ranges: [[9, 12]] } })
    expect(w.findAll('mark').map(m => m.text())).toEqual(['hit'])
    expect(w.find('b').exists()).toBe(false)
    expect(w.text()).toBe('<b>x</b> hit')
  })
})
