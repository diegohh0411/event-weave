import { describe, expect, it } from 'vitest'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import App from '../../app/app.vue'

describe('layout', () => {
  it('shows the Builder, Runs and Compare links', async () => {
    const wrapper = await mountSuspended(App, { route: '/' })
    const links = wrapper.findAll('a').map(a => [a.text(), a.attributes('href')])
    expect(links).toEqual(expect.arrayContaining([['Builder', '/'], ['Runs', '/runs'], ['Compare', '/compare']]))
  })
})
