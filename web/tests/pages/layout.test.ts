import { describe, expect, it } from 'vitest'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import App from '../../app/app.vue'

describe('layout', () => {
  it('shows the sections in workflow order and marks the current one', async () => {
    const wrapper = await mountSuspended(App, { route: '/' })
    const nav = wrapper.find('nav[aria-label=Primary]')
    const links = nav.findAll('a').map(a => [a.text(), a.attributes('href')])
    expect(links).toEqual([['Crawls', '/crawls'], ['New run', '/'], ['Runs', '/runs'], ['Compare', '/compare']])
    expect(nav.find('a[aria-current=page]').text()).toBe('New run')
  })
})
