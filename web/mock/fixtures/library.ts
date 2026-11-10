import type { Schemas } from './types'

export const libraryDocuments: Schemas['LibraryDocumentDetail'][] = [
  { id: 'library-flood-v2', article_id: 'flood', version: 2, versions: 2, title: 'Flood response grows', outlet: 'example.com', language: 'en', url: 'https://example.com/flood', published_at: '2026-11-01T12:00:00Z', byline: 'A Reporter', text: 'Flood response continues.', removed_at: null, removed_reason: null, last_checked_at: '2026-11-02T00:00:00Z', next_check_at: '2026-11-08T12:00:00Z', rechecks: [
    { checked_at: '2026-11-02T00:00:00Z', stage: 0, outcome: 'edited', http_status: 200, final_url: null, reason: null, document_id: 'library-flood-v2' }
  ], history: [
    { id: 'library-flood-v2', version: 2, superseded_at: null }, { id: 'library-flood-v1', version: 1, superseded_at: '2026-11-02T00:00:00Z' }
  ] },
  { id: 'library-flood-v1', article_id: 'flood', version: 1, versions: 2, title: 'Flood response', outlet: 'example.com', language: 'en', url: 'https://example.com/flood', published_at: '2026-11-01T12:00:00Z', byline: 'A Reporter', text: 'Initial report.', removed_at: null, removed_reason: null, last_checked_at: '2026-11-02T00:00:00Z', next_check_at: '2026-11-08T12:00:00Z', rechecks: [
    { checked_at: '2026-11-02T00:00:00Z', stage: 0, outcome: 'edited', http_status: 200, final_url: null, reason: null, document_id: 'library-flood-v2' }
  ], history: [
    { id: 'library-flood-v2', version: 2, superseded_at: null }, { id: 'library-flood-v1', version: 1, superseded_at: '2026-11-02T00:00:00Z' }
  ] }
]
