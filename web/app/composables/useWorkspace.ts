import { useApi } from '../api/client'
import type { components } from '../api/schema'

type RunSummary = components['schemas']['RunSummary']
type Crawl = components['schemas']['CrawlOut']

export interface RecentItem { id: string, label: string, status: string, to: string, when: string }

const ago = (iso: string) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (!Number.isFinite(m)) return ''
  if (m < 60) return `${Math.max(1, m)}m`
  if (m < 1440) return `${Math.round(m / 60)}h`
  return `${Math.round(m / 1440)}d`
}

const runItem = (r: RunSummary): RecentItem => ({ id: r.id, label: r.name, status: r.state, to: `/runs/${r.id}`, when: ago(r.created_at) })
const crawlItem = (c: Crawl): RecentItem => ({
  id: c.id, label: c.request.sites.map(s => s.domain).join(', ') || c.id, status: c.status, to: `/crawls/${c.id}`, when: ago(c.created_at)
})

/**
 * Shared sidebar state: recent runs and crawls, API reachability and the collapsed flag.
 * Every fetch fails quietly; the sidebar only reports whether the API answered.
 */
export function useWorkspace() {
  const api = useApi()
  const runs = useState<RecentItem[]>('ew-runs', () => [])
  const crawls = useState<RecentItem[]>('ew-crawls', () => [])
  const apiOnline = useState<boolean | null>('ew-api-online', () => null)
  const collapsed = useState<boolean>('ew-sidebar-collapsed', () => {
    try { return localStorage.getItem('ew-sidebar-collapsed') === '1' } catch { return false }
  })

  function setCollapsed(v: boolean) {
    collapsed.value = v
    try { localStorage.setItem('ew-sidebar-collapsed', v ? '1' : '0') } catch { /* preference only */ }
  }

  async function refresh(limit = 6) {
    const [r, c, h] = await Promise.allSettled([
      api.GET('/runs', { params: { query: { offset: 0, limit } } }),
      api.GET('/crawls', { params: { query: { offset: 0, limit } } }),
      api.GET('/health')
    ])
    if (r.status === 'fulfilled' && r.value.data) runs.value = r.value.data.items.map(runItem)
    if (c.status === 'fulfilled' && c.value.data) crawls.value = c.value.data.items.map(crawlItem)
    apiOnline.value = h.status === 'fulfilled' && !!h.value.data
  }

  return { runs, crawls, apiOnline, collapsed, setCollapsed, refresh }
}
