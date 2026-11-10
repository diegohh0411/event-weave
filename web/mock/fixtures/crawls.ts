import type { Schemas } from './types'

export const CRAWL_STORED = '00000000-0000-4000-8000-000000000101'
export const CRAWL_PAYWALLED = '00000000-0000-4000-8000-000000000102'
export const CRAWL_QUEUED = '00000000-0000-4000-8000-000000000103'
const site = { domain: 'example.com', name: 'example.com', language: 'en' as const, per_day: 3, feeds: [], sitemaps: [] }
export const emptyCounters = (): Schemas['CrawlCounters'] => ({
  discovered: 0, fetched: 0, stored: 0, existing: 0, duplicates: 0, rejected: 0, errors: 0, updated: 0, unchanged: 0, removed: 0, reasons: {}
})
const base: Schemas['CrawlOut'] = {
  id: CRAWL_QUEUED, site_id: null, trigger: 'adhoc', request: { sites: [site], days: 1 }, status: 'queued',
  created_at: '2026-11-09T10:00:00Z', started_at: null, finished_at: null, counters: emptyCounters(), error: null
}
export const queued = base
export const stored: Schemas['CrawlOut'] = {
  ...base, id: CRAWL_STORED, status: 'completed', started_at: '2026-11-09T10:01:00Z', finished_at: '2026-11-09T10:02:00Z',
  counters: { ...emptyCounters(), discovered: 2, fetched: 1, stored: 1, existing: 1 }
}
export const paywalled: Schemas['CrawlOut'] = {
  ...base, id: CRAWL_PAYWALLED, status: 'completed', started_at: '2026-11-09T10:01:00Z', finished_at: '2026-11-09T10:02:00Z',
  counters: { ...emptyCounters(), discovered: 1, fetched: 1, rejected: 1, reasons: { paywall: 1 } }
}
export const crawls = [queued, stored, paywalled]
export const document: Schemas['CrawlDocumentOut'] = {
  id: 'crawl-document-1', title: 'A local event', byline: 'Reporter', outlet: 'example.com',
  url: 'https://example.com/news/1', language: 'en', published_at: '2026-11-09T09:00:00Z', text: 'An event happened in town.'
}
export const articles: Record<string, Schemas['CrawlArticleOut'][]> = {
  [CRAWL_QUEUED]: [],
  [CRAWL_STORED]: [
    { canonical_url: document.url, url: document.url, outlet: 'example.com', status: 'stored', reason: null,
      document_id: document.id, fetched: true, updated_at: '2026-11-09T10:02:00Z' },
    { canonical_url: 'https://example.com/news/2', url: 'https://example.com/news/2', outlet: 'example.com', status: 'existing',
      reason: null, document_id: document.id, fetched: false, updated_at: '2026-11-09T10:02:00Z' }
  ],
  [CRAWL_PAYWALLED]: [{ canonical_url: 'https://example.com/news/locked', url: 'https://example.com/news/locked',
    outlet: 'example.com', status: 'rejected', reason: 'paywall', document_id: null, fetched: true, updated_at: '2026-11-09T10:02:00Z' }]
}
export const sources: Record<string, Schemas['CrawlSourceOut'][]> = {
  [CRAWL_QUEUED]: [],
  [CRAWL_STORED]: [{ url: 'https://example.com/feed', site: 'example.com', kind: 'feed', status: 'completed', error: null }],
  [CRAWL_PAYWALLED]: [{ url: 'https://example.com/sitemap.xml', site: 'example.com', kind: 'sitemap', status: 'error', error: 'Discovery failed' }]
}
