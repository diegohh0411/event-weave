import type { Ok } from './types'
import { DOC_EN } from './ids'

// Production shapes (weave_enrichers.report.sample): the sentence carries its trigger as ⟦ ⟧; each of the
// four fields is `{text, spans}` or null; `discarded` maps a field to a count.
const field = (text: string) => ({ text, spans: [[0, Array.from(text).length]] })

type Sample = Ok<'/runs/{run_id}/enrichments/sample', 'get'>['items'][number]

export const samples: Sample[] = [
  {
    id: 'a0000000-0000-4000-8000-000000000001', mention_id: 'm0000000-0000-4000-8000-000000000001', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'The river Tisza ⟦flooded⟧ the lower streets of Szeged on Friday.',
    enrichment: { action: field('flooded'), participants: field('River Tisza'), location: field('Szeged'), time: field('Friday') },
    discarded: {}
  },
  {
    id: 'a0000000-0000-4000-8000-000000000002', mention_id: 'm0000000-0000-4000-8000-000000000004', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'Meanwhile, dockworkers in Rotterdam ⟦walked out⟧ over pay.',
    enrichment: { action: field('walked out'), participants: field('dockworkers'), location: field('Rotterdam'), time: null },
    discarded: { time: 1 }
  },
  {
    id: 'a0000000-0000-4000-8000-000000000003', mention_id: 'm0000000-0000-4000-8000-000000000007', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'Officials said a sandbag wall was ⟦holding⟧.',
    enrichment: { action: null, participants: null, location: null, time: null },
    discarded: { misaligned: 2 }
  }
]
