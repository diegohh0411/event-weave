import type { Ok } from './types'
import { DOC_EN } from './ids'

type Sample = Ok<'/runs/{run_id}/enrichments/sample', 'get'>['items'][number]

export const samples: Sample[] = [
  {
    id: 'a0000000-0000-4000-8000-000000000001', mention_id: 'm0000000-0000-4000-8000-000000000001', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'The river Tisza flooded the lower streets of Szeged on Friday.',
    enrichment: { event_type: 'natural_disaster', significance: 0.92, participants: ['River Tisza'], location: 'Szeged' },
    discarded: {}
  },
  {
    id: 'a0000000-0000-4000-8000-000000000002', mention_id: 'm0000000-0000-4000-8000-000000000004', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'Meanwhile, dockworkers in Rotterdam walked out over pay.',
    enrichment: { event_type: 'labor_action', significance: 0.61, participants: ['dockworkers'], location: 'Rotterdam' },
    discarded: { time: 'no date in window' }
  },
  {
    id: 'a0000000-0000-4000-8000-000000000003', mention_id: 'm0000000-0000-4000-8000-000000000007', document_id: DOC_EN,
    enricher: 'clairic', enricher_version: '1',
    sentence: 'Officials said a sandbag wall was holding.',
    enrichment: {},
    discarded: { event_type: 'confidence below 0.3', significance: 'confidence below 0.3' }
  }
]
