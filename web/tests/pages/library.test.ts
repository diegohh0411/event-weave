import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import { createMockHandler } from '../../mock/server'

beforeEach(() => { const handler = createMockHandler(); vi.stubGlobal('fetch', (input: Request | string, init?: RequestInit) => handler(input instanceof Request ? input : new Request(input, init))) })
afterEach(() => vi.unstubAllGlobals())

it('renders library documents and keeps search in the URL', async () => {
  const { default: Library } = await import('../../app/pages/library/index.vue')
  const w = await mountSuspended(Library, { route: '/library?search=Flood' })
  await vi.waitFor(() => expect(w.text()).toContain('Flood'))
  expect((w.find('input[name=search]').element as HTMLInputElement).value).toBe('Flood')
  await w.find('input[name=search]').setValue('Storm')
  await vi.waitFor(() => expect(useRoute().query.search).toBe('Storm'))
  w.unmount()
})

it('opens a document and shows its version history', async () => {
  const { default: Detail } = await import('../../app/pages/library/[id].vue')
  const w = await mountSuspended(Detail, { route: '/library/library-flood-v2' })
  await vi.waitFor(() => expect(w.text()).toContain('Flood response continues.'))
  expect(w.find('a[href="/library/library-flood-v1"]').text()).toContain('Version 1')
  w.unmount()
})
