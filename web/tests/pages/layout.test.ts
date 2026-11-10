import { describe, expect, it } from 'vitest'
import { mountSuspended } from '@nuxt/test-utils/runtime'
import App from '../../app/app.vue'

describe('layout', () => {
  it('puts the new-run action first, then the sections, and marks the current one', async () => {
    const wrapper = await mountSuspended(App, { route: '/' })
    const nav = wrapper.find('nav[aria-label=Primary]')
    const links = nav.findAll('a').map(a => [a.text(), a.attributes('href')])
    expect(links).toEqual([['New run', '/'], ['Library', '/library'], ['Sites', '/sites'], ['Crawls', '/crawls'], ['Runs', '/runs'], ['Compare', '/compare']])
    expect(nav.find('a[aria-current=page]').text()).toBe('New run')
    expect(nav.find('button').text()).toContain('Search')
  })

  it('collapses the sidebar to icons and remembers it', async () => {
    const wrapper = await mountSuspended(App, { route: '/runs' })
    const toggle = wrapper.find('button[aria-label="Collapse sidebar"]')
    await toggle.trigger('click')
    const nav = wrapper.find('nav[aria-label=Primary]')
    expect(nav.findAll('a').map(a => a.text())).toEqual(['', '', '', '', '', ''])
    expect(localStorage.getItem('ew-sidebar-collapsed')).toBe('1')
    await wrapper.find('button[aria-label="Expand sidebar"]').trigger('click')
    expect(localStorage.getItem('ew-sidebar-collapsed')).toBe('0')
  })
})
