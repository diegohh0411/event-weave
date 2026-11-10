import type { Schemas } from './types'

export const libraryDocuments: Schemas['LibraryDocumentDetail'][] = [
  { id: 'library-flood-v2', article_id: 'flood', version: 2, versions: 2, title: 'Flood response grows', outlet: 'example.com', language: 'en', url: 'https://example.com/flood', published_at: '2026-11-01T12:00:00Z', byline: 'A Reporter', text: 'Flood response continues.', history: [
    { id: 'library-flood-v2', version: 2, superseded_at: null }, { id: 'library-flood-v1', version: 1, superseded_at: '2026-11-02T00:00:00Z' }
  ] },
  { id: 'library-flood-v1', article_id: 'flood', version: 1, versions: 2, title: 'Flood response', outlet: 'example.com', language: 'en', url: 'https://example.com/flood', published_at: '2026-11-01T12:00:00Z', byline: 'A Reporter', text: 'Initial report.', history: [
    { id: 'library-flood-v2', version: 2, superseded_at: null }, { id: 'library-flood-v1', version: 1, superseded_at: '2026-11-02T00:00:00Z' }
  ] }
]
