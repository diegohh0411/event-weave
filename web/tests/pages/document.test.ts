import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import DocumentPage from '../../app/pages/runs/[id]/documents/[did].vue'
import { createMockHandler } from '../../mock/server'
import { CLUSTER_FLOOD, DOC_EN, RUN_LLM } from '../../mock/fixtures/ids'
import { clusterColors } from '../../app/utils/clusterColors'
import { docEn } from '../../mock/fixtures/documents'

const mounted: { unmount: () => void }[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
let override: ((req: Request) => Response | undefined) | undefined

beforeEach(() => {
  override = undefined
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    return override?.(req.clone()) ?? handler(req)
  })
})
afterEach(() => {
  mounted.splice(0).forEach(w => w.unmount())
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

const mountDoc = async (did = DOC_EN) => {
  const w = await mountSuspended(DocumentPage, { route: `/runs/${RUN_LLM}/documents/${did}` })
  mounted.push(w)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}

describe('document view', () => {
  it('shows the header and the text intact', async () => {
    const w = await mountDoc()
    for (const t of ['Tisza floods Szeged streets', 'Danube Daily', '2026-03-14', 'https://example.test/danube-daily/tisza-floods-szeged']) {
      expect(w.text()).toContain(t)
    }
    expect(w.find('[data-test=text]').text()).toBe(docEn.text)
  })

  it('colours mentions by cluster; a mention without a cluster gets no fill, only a dashed underline', async () => {
    const w = await mountDoc()
    const spans = w.findAll('[data-test=mention]')
    expect(spans.map(s => s.text())).toEqual(['flooded', 'evacuated', 'walked out', 'strike', 'holding'])
    const colours = clusterColors(docEn.mentions.map(m => m.cluster_id))
    const fill = (i: number) => (spans[i]!.element as HTMLElement).style.backgroundColor
    expect(fill(0)).not.toBe('')
    expect(fill(0)).toBe(fill(1))
    expect(fill(2)).toBe(fill(3))
    expect(fill(0)).not.toBe(fill(2))
    expect(colours.get(CLUSTER_FLOOD)).toBeTruthy()
    expect(fill(4)).toBe('')
    expect(spans[4]!.classes()).toContain('border-dashed')
    expect(spans[0]!.classes()).not.toContain('border-dashed')
  })

  it('highlights by code point in a text with astral characters', async () => {
    const text = '😀😀 river flooded here'
    const start = Array.from('😀😀 river ').length
    override = (r) => {
      if (!new URL(r.url).pathname.endsWith(`/documents/${DOC_EN}`)) return undefined
      return json({ ...docEn, text, mentions: [{ id: 'm1', start, end: start + 7, cluster_id: null, cluster_size: null, enrichment: null }] })
    }
    const w = await mountDoc()
    expect(w.findAll('[data-test=mention]').map(s => s.text())).toEqual(['flooded'])
    expect(w.find('[data-test=text]').text()).toBe(text)
  })

  it('hover_shows_four_fields', async () => {
    const w = await mountDoc()
    await w.findAll('[data-test=mention]')[0]!.trigger('pointerenter')
    await vi.waitFor(() => expect(document.body.textContent).toContain('Szeged, Hungary'))
    const body = document.body.textContent ?? ''
    for (const t of ['flooded', 'River Tisza', 'Szeged, Hungary', '2026-03-14']) expect(body).toContain(t)
    expect(document.body.querySelector(`a[href="/runs/${RUN_LLM}/clusters/${CLUSTER_FLOOD}"]`)).not.toBeNull()
  })

  it('shows an error alert when the document cannot load', async () => {
    override = r => new URL(r.url).pathname.endsWith(`/documents/${DOC_EN}`) ? json({ detail: 'nope' }, 404) : undefined
    const w = await mountDoc()
    expect(w.text()).toContain('Could not load the document')
  })
})
