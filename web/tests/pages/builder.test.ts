import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import Builder from '../../app/pages/index.vue'
import { createMockHandler } from '../../mock/server'
import { estimates } from '../../mock/fixtures/runs'
import { deployments } from '../../mock/fixtures/deployments'

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }))
mockNuxtImport('navigateTo', () => navigateTo)

type Override = (req: Request) => Response | undefined
let overrides: Override[] = []
let calls: string[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

beforeEach(() => {
  overrides = []
  calls = []
  navigateTo.mockReset()
  const handler = createMockHandler()
  vi.stubGlobal('fetch', async (input: Request | string, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init)
    const path = new URL(req.url).pathname
    calls.push(`${req.method} ${path}`)
    for (const o of overrides) {
      const r = o(req.clone())
      if (r) return r
    }
    return handler(req)
  })
})
afterEach(() => vi.unstubAllGlobals())

const mountBuilder = async () => {
  const w = await mountSuspended(Builder)
  await vi.waitFor(() => expect(w.text()).not.toContain('Loading'))
  await flushPromises()
  return w
}
const button = (w: Awaited<ReturnType<typeof mountBuilder>>, label: string) =>
  w.findAll('button').find(b => b.text().includes(label))!

describe('builder page', () => {
  it('offers the adapters and a None choice for tag', async () => {
    const w = await mountBuilder()
    expect(w.text()).toContain('Provider')
    expect(w.text()).toContain('Tag')
    expect(w.text()).toContain('None')
    // MCECR languages come from its settings schema
    expect(w.text()).toContain('Languages')
  })

  it('disables Launch while validation has problems', async () => {
    overrides.push(r => new URL(r.url).pathname === '/runs/validate'
      ? json({ problems: ['deployment local-qwen (llm) is not ready: connection refused'] }, 422)
      : undefined)
    const w = await mountBuilder()
    expect(button(w, 'Launch').attributes('disabled')).toBeUndefined()
    await button(w, 'Validate').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('local-qwen (llm) is not ready'))
    expect(button(w, 'Launch').attributes('disabled')).toBeDefined()
  })

  it('renders "No estimate yet" from the estimate fixture', async () => {
    overrides.push(r => new URL(r.url).pathname === '/runs/estimate' ? json(estimates.unpriced) : undefined)
    const w = await mountBuilder()
    await button(w, 'Validate').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('No estimate yet: deployment local-qwen has no price per token'))
    expect(w.text()).not.toContain('$0')
  })

  it('shows the priced estimate per stage', async () => {
    const w = await mountBuilder()
    await button(w, 'Validate').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('1800 / 6230'))
  })

  it('disables Launch when a capability has no ready deployment', async () => {
    const none = { ...deployments, items: deployments.items.filter(d => d.capability !== 'embeddings') }
    overrides.push(r => new URL(r.url).pathname === '/deployments' ? json({ ...none, total: none.items.length }) : undefined)
    const w = await mountBuilder()
    expect(w.text()).toContain('No ready deployment for embeddings')
    expect(button(w, 'Launch').attributes('disabled')).toBeDefined()
  })

  it('creates, starts and opens the run on Launch', async () => {
    const w = await mountBuilder()
    await button(w, 'Launch').trigger('click')
    await vi.waitFor(() => expect(navigateTo).toHaveBeenCalled())
    const posts = calls.filter(c => c.startsWith('POST'))
    expect(posts.at(-2)).toBe('POST /runs')
    expect(posts.at(-1)).toMatch(/^POST \/runs\/.+\/start$/)
    expect(navigateTo.mock.calls[0]![0]).toMatch(/^\/runs\/.+/)
  })

  it('shows the problems of a rejected launch and does not start', async () => {
    overrides.push(r => r.method === 'POST' && new URL(r.url).pathname === '/runs' ? json({ problems: ['nope: bad'] }, 422) : undefined)
    const w = await mountBuilder()
    await button(w, 'Launch').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('nope: bad'))
    expect(calls.some(c => c.endsWith('/start'))).toBe(false)
    expect(navigateTo).not.toHaveBeenCalled()
  })
})
