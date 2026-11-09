import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import SpotCheck from '../../app/pages/runs/[id]/spot-check.vue'
import { createMockHandler } from '../../mock/server'
import { RUN_LLM } from '../../mock/fixtures/ids'

let calls: URL[] = []
const mounted: { unmount: () => void }[] = []

beforeEach(() => {
  calls = []
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    calls.push(new URL(req.url))
    return handler(req)
  })
})
afterEach(() => {
  mounted.splice(0).forEach(w => w.unmount())
  vi.unstubAllGlobals()
})

const sampleCalls = () => calls.filter(u => u.pathname === `/runs/${RUN_LLM}/enrichments/sample`)
const mountPage = async (query = '') => {
  const w = await mountSuspended(SpotCheck, { route: `/runs/${RUN_LLM}/spot-check${query}` })
  mounted.push(w)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}

describe('enrichment spot check', () => {
  it('shows each row with the trigger marked, the four fields and the discarded counts', async () => {
    const w = await mountPage('?seed=1&n=10')
    const rows = w.findAll('[data-test=sample]')
    expect(rows.length).toBe(3)
    const flood = rows.find(r => r.text().includes('Tisza flooded'))!
    expect(flood.findAll('mark').map(m => m.text())).toEqual(['flooded'])
    for (const t of ['natural_disaster', 'River Tisza', 'Szeged']) expect(flood.text()).toContain(t)
    const strike = rows.find(r => r.text().includes('Rotterdam walked out'))!
    expect(strike.findAll('mark').map(m => m.text())).toEqual(['walked out'])
    expect(strike.text()).toContain('no date in window')
    expect(strike.text()).toContain('Discarded (1)')
  })

  it('offers the run config enricher and the sample enrichers, and filters by it', async () => {
    const w = await mountPage('?seed=1')
    const select = w.find('[data-test=enricher]')
    expect(select.findAll('option').map(o => o.attributes('value'))).toEqual(['', 'clairic'])
    await select.setValue('clairic')
    await vi.waitFor(() => expect(sampleCalls().at(-1)!.searchParams.get('enricher')).toBe('clairic'))
    expect(useRoute().query.enricher).toBe('clairic')
  })

  it('spot_check_reseed_changes_query', async () => {
    const w = await mountPage('?seed=5&n=2')
    expect(sampleCalls().at(-1)!.searchParams.get('seed')).toBe('5')
    expect(sampleCalls().at(-1)!.searchParams.get('limit')).toBe('2')
    expect((w.find('input[data-test=seed]').element as HTMLInputElement).value).toBe('5')
    await w.find('[data-test=reshuffle]').trigger('click')
    await vi.waitFor(() => expect(sampleCalls().at(-1)!.searchParams.get('seed')).not.toBe('5'))
    const seed = sampleCalls().at(-1)!.searchParams.get('seed')
    expect(useRoute().query.seed).toBe(seed)
    expect(useRoute().query.n).toBe('2')
    await w.find('input[data-test=n]').setValue('1')
    await vi.waitFor(() => expect(sampleCalls().at(-1)!.searchParams.get('limit')).toBe('1'))
  })
})
