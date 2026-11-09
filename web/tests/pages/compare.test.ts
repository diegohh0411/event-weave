import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import ComparePage from '../../app/pages/compare.vue'
import { createMockHandler } from '../../mock/server'
import { RUN_LLM, RUN_NEW, RUN_PAPER } from '../../mock/fixtures/ids'
import { HINDI_CROSS_SKIPPED } from '../../mock/fixtures/scores'

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
  vi.unstubAllGlobals()
})

const compareCalls = () => calls.filter(u => u.pathname === '/compare')
const mountCompare = async (ids: string[]) => {
  const qs = ids.map(i => `run_id=${i}`).join('&')
  const w = await mountSuspended(ComparePage, { route: `/compare${qs ? `?${qs}` : ''}` })
  mounted.push(w)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}
const tables = (w: Awaited<ReturnType<typeof mountCompare>>, track?: string) =>
  w.findAll(`[data-test=score-table]${track ? `[data-track="${track}"]` : ''}`)

describe('compare page', () => {
  it('paper_rows_only_in_track1_tables', async () => {
    const w = await mountCompare([RUN_PAPER, RUN_LLM])
    expect(tables(w, '1').length).toBeGreaterThan(0)
    expect(tables(w, '2').length).toBeGreaterThan(0)
    for (const t of tables(w, '2')) expect(t.text()).not.toContain('MCECR paper')
    // Paper rows appear once per (language, setting, model) and never twice for the same table.
    const en = tables(w, '1').find(t => t.attributes('data-language') === 'en')!
    expect(en.findAll('[data-test=paper-row]').filter(r => r.text().includes('MCECR paper (hierarchical)'))).toHaveLength(2) // within + cross
    // No paper numbers at all when only a Track 2 run is compared.
    const w2 = await mountCompare([RUN_LLM])
    expect(w2.text()).not.toContain('MCECR paper')
  })

  it('paper_rows_labelled', async () => {
    const w = await mountCompare([RUN_PAPER])
    const en = tables(w, '1').find(t => t.attributes('data-language') === 'en')!
    const row = en.findAll('[data-test=paper-row]').find(r => r.text().includes('MCECR paper (hierarchical)'))!
    expect(row.classes()).toContain('text-muted')
    expect(row.text()).toContain('monolingual')
    expect(row.text()).toContain('our setting is harder')
    expect(row.text()).toContain('74.0') // paper MUC F1, already a percentage
    expect(en.text()).toContain('MCECR paper (joint)')
  })

  it('unchecked_banner', async () => {
    const w = await mountCompare([RUN_PAPER])
    expect(w.text()).toContain('Paper numbers UNCHECKED')
    const checked = await mountCompare([RUN_LLM])
    expect(checked.text()).not.toContain('Paper numbers UNCHECKED')
  })

  it('hindi_cross_shows_reason', async () => {
    const w = await mountCompare([RUN_PAPER])
    const hi = tables(w, '1').find(t => t.attributes('data-language') === 'hi')!
    expect(hi.text()).toContain(HINDI_CROSS_SKIPPED)
  })

  it('unevaluated_run_message', async () => {
    const w = await mountCompare([RUN_NEW])
    expect(w.text()).toContain(`Not evaluated (run weave eval ${RUN_NEW})`)
    expect(w.text()).not.toMatch(/\b0\.0\b/)
  })

  it('shows paper rows even when no Track 1 run is evaluated', async () => {
    const w = await mountCompare([RUN_NEW])
    const en = tables(w, '1').find(t => t.attributes('data-language') === 'en')!
    expect(en.findAll('[data-test=run-row]')).toHaveLength(0)
    expect(en.text()).toContain('MCECR paper (hierarchical)')
  })

  it('keeps monolingual and transfer paper rows apart', async () => {
    const handler = createMockHandler()
    overrides.push(async (req) => {
      if (new URL(req.url).pathname !== '/compare') return handler(req)
      const body = await (await handler(req)).json()
      body.paper.push({ ...body.paper[0], regime: 'transfer', label: 'like for like (both tuned on English only)', metric: 'muc', paper: { hierarchical: { f1: 50, precision: 51, recall: 49 } } })
      return json(body)
    })
    const w = await mountCompare([RUN_PAPER])
    const en = tables(w, '1').find(t => t.attributes('data-language') === 'en')!
    const rows = en.findAll('[data-test=paper-row]').filter(r => r.text().includes('MCECR paper (hierarchical)') && r.text().includes('within'))
    expect(rows).toHaveLength(2)
    expect(rows.map(r => /monolingual|transfer/.exec(r.text())![0]).sort()).toEqual(['monolingual', 'transfer'])
    expect(rows.find(r => r.text().includes('transfer'))!.text()).toContain('50.0')
    expect(rows.find(r => r.text().includes('monolingual'))!.text()).toContain('74.0')
  })

  it('reads the trigger entry for the table language', async () => {
    const handler = createMockHandler()
    overrides.push(async (req) => {
      if (new URL(req.url).pathname !== '/compare') return handler(req)
      const body = await (await handler(req)).json()
      body.runs[0].scores.push({ ...body.runs[0].scores[0], language: 'es' })
      return json(body)
    })
    const w = await mountCompare([RUN_LLM])
    const row = (lang: string) => tables(w, '2').find(t => t.attributes('data-language') === lang)!.find('[data-test=run-row]').text()
    expect(row('en')).toContain('84.0')
    expect(row('es')).toContain('72.0')
    expect(row('es')).not.toContain('84.0')
  })

  it('formats scores, intervals, trigger and cost columns', async () => {
    const w = await mountCompare([RUN_PAPER, RUN_LLM])
    const en1 = tables(w, '1').find(t => t.attributes('data-language') === 'en')!
    const ours = en1.find('[data-test=run-row]')
    expect(ours.text()).toContain('71.0 [68.0–74.0]')
    expect(ours.text()).toContain('4210.5')
    const t2 = tables(w, '2')[0]!
    expect(t2.text()).toContain('Trigger P')
    expect(t2.text()).toContain('84.0')
    expect(t2.text()).toContain('0.55')
    expect(t2.text()).toContain('912.3')
    expect(t2.find('[data-test=run-row]').text()).toContain('6') // failures
    expect(en1.text()).not.toContain('Trigger P')
  })

  it('wraps tables in their own scroll container', async () => {
    const w = await mountCompare([RUN_PAPER])
    expect(w.find('[data-test=score-table]').element.closest('.overflow-x-auto')).not.toBeNull()
  })

  it('keeps the selected runs in the URL and refetches', async () => {
    const w = await mountCompare([RUN_PAPER])
    expect(compareCalls().at(-1)!.searchParams.getAll('run_id')).toEqual([RUN_PAPER])
    const box = w.find(`input[type=checkbox][value="${RUN_LLM}"]`)
    await vi.waitFor(() => expect(box.exists()).toBe(true))
    await box.setValue(true)
    await vi.waitFor(() => expect(compareCalls().at(-1)!.searchParams.getAll('run_id')).toEqual([RUN_PAPER, RUN_LLM]))
    expect([].concat(useRoute().query.run_id as never)).toEqual([RUN_PAPER, RUN_LLM])
  })

  it('asks for a run when none is selected and does not fetch', async () => {
    const w = await mountCompare([])
    expect(w.text()).toContain('Select at least one run')
    expect(compareCalls()).toHaveLength(0)
  })

  it('shows an error alert when the API rejects', async () => {
    overrides.push(req => (new URL(req.url).pathname === '/compare' ? json({ detail: 'boom' }, 500) : undefined))
    const w = await mountCompare([RUN_PAPER])
    expect(w.text()).toContain('Could not load the comparison')
  })

  it('ignores a stale response', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => (release = r))
    const handler = createMockHandler()
    let n = 0
    overrides.push((req) => {
      if (new URL(req.url).pathname !== '/compare') return undefined
      if (n++ === 0) return gate.then(() => handler(req)) as unknown as Response
      return undefined
    })
    const w = await mountSuspended(ComparePage, { route: `/compare?run_id=${RUN_PAPER}` })
    mounted.push(w)
    await navigateTo(`/compare?run_id=${RUN_LLM}`)
    await vi.waitFor(() => expect(tables(w, '2').length).toBeGreaterThan(0))
    release()
    await flushPromises()
    expect(tables(w, '1')).toHaveLength(0)
  })

  describe('eight run limit', () => {
    const ids = Array.from({ length: 10 }, (_, n) => `0b8f6c1e-5d1a-4f3e-9a52-3c1d2e4f60${String(n).padStart(2, '0')}`)
    const stubRuns = () => {
      const handler = createMockHandler()
      overrides.push((req) => {
        if (new URL(req.url).pathname !== '/runs') return undefined
        return (async () => {
          const body = await (await handler(req)).json()
          const base = body.items[0]
          return json({ ...body, items: ids.map((id, n) => ({ ...base, id, name: `run ${n}` })), total: ids.length })
        })()
      })
    }
    const boxes = (w: Awaited<ReturnType<typeof mountCompare>>) => w.findAll('input[type=checkbox]')

    it('disables further picks once eight are selected, with a hint', async () => {
      stubRuns()
      const w = await mountCompare(ids.slice(0, 8))
      await vi.waitFor(() => expect(boxes(w)).toHaveLength(10))
      const rest = boxes(w).filter(b => !(b.element as HTMLInputElement).checked)
      expect(rest).toHaveLength(2)
      for (const b of rest) expect(b.attributes('disabled')).toBeDefined()
      for (const b of boxes(w).filter(b => (b.element as HTMLInputElement).checked)) expect(b.attributes('disabled')).toBeUndefined()
      expect(w.text()).toContain('at most 8 runs')
    })

    it('keeps other runs selectable below the limit', async () => {
      stubRuns()
      const w = await mountCompare(ids.slice(0, 7))
      await vi.waitFor(() => expect(boxes(w)).toHaveLength(10))
      for (const b of boxes(w)) expect(b.attributes('disabled')).toBeUndefined()
    })

    it('truncates a longer URL to eight runs and says so', async () => {
      stubRuns()
      const w = await mountCompare(ids)
      expect(compareCalls().at(-1)!.searchParams.getAll('run_id')).toEqual(ids.slice(0, 8))
      expect(w.text()).toContain('Only the first 8 of 10 runs are compared')
    })
  })
})
