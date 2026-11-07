import type { Ok } from './types'
import { CLUSTER_FLOOD, CLUSTER_SINGLE, CLUSTER_STRIKE, DOC_EN, DOC_ES, DOC_HI } from './ids'

type Summary = Ok<'/runs/{run_id}/clusters', 'get'>['items'][number]
type Member = Ok<'/runs/{run_id}/clusters/{cluster_id}/members', 'get'>['items'][number]

export const clusterSummaries: Summary[] = [
  {
    id: CLUSTER_FLOOD, size: 3, languages: ['en', 'es'], event_types: ['natural_disaster'], max_significance: 0.92, partition: 'topic-14',
    card: { action: 'flooded', participants: ['River Tisza', 'Szeged'], location: 'Szeged, Hungary', time: '2026-03-14' }
  },
  {
    id: CLUSTER_STRIKE, size: 2, languages: ['en'], event_types: ['labor_action'], max_significance: 0.61, partition: 'topic-9',
    card: { action: 'walked out', participants: ['dockworkers'], location: 'Rotterdam', time: '2026-03-02' }
  },
  {
    id: CLUSTER_SINGLE, size: 1, languages: ['hi'], event_types: [], max_significance: null, partition: null, card: null
  }
]

const floodMembers: Member[] = [
  {
    mention_id: 'm0000000-0000-4000-8000-000000000001', document_id: DOC_EN, document_title: 'Tisza floods Szeged streets', language: 'en',
    text: 'flooded', sentence: 'The river Tisza flooded the lower streets of Szeged on Friday.', trigger: [21, 28],
    event_type: 'natural_disaster', significance: 0.92,
    // The first mention seeds the cluster: no decision was taken for it.
    decision: null
  },
  {
    mention_id: 'm0000000-0000-4000-8000-000000000002', document_id: DOC_EN, document_title: 'Tisza floods Szeged streets', language: 'en',
    text: 'evacuated', sentence: 'Hundreds of residents were evacuated before dawn.', trigger: [29, 38],
    event_type: 'natural_disaster', significance: 0.74,
    decision: {
      outcome: 'join', adapter: 'llm-judge', deployment: 'claude-sonnet', noul: 0.04, reason: 'Same flood, same morning.',
      probabilities: { join: 0.93, new: 0.07 },
      candidates: [
        {
          cluster_id: CLUSTER_FLOOD, score: 0.88, label: 'A', field_scores: { action: 0.9, participants: 0.8, location: 1, time: 1 }
        },
        { cluster_id: CLUSTER_STRIKE, score: 0.12, label: 'B', field_scores: { action: 0.1, participants: 0, location: 0, time: 0.4 } }
      ]
    }
  },
  {
    mention_id: 'm0000000-0000-4000-8000-000000000003', document_id: DOC_ES, document_title: 'El Tisza inunda Szeged', language: 'es',
    text: 'inundó', sentence: 'El río Tisza inundó las calles bajas de Szeged el viernes.', trigger: [14, 20],
    event_type: 'natural_disaster', significance: null,
    decision: {
      outcome: 'join', adapter: 'threshold', deployment: null, noul: null, reason: null, probabilities: null,
      candidates: [
        { cluster_id: CLUSTER_FLOOD, score: 0.91, label: null, field_scores: null }
      ]
    }
  }
]

const strikeMembers: Member[] = [
  {
    mention_id: 'm0000000-0000-4000-8000-000000000004', document_id: DOC_EN, document_title: 'Tisza floods Szeged streets', language: 'en',
    text: 'walked out', sentence: 'Meanwhile, dockworkers in Rotterdam walked out over pay.', trigger: [34, 44],
    event_type: 'labor_action', significance: 0.61, decision: null
  },
  {
    mention_id: 'm0000000-0000-4000-8000-000000000005', document_id: DOC_EN, document_title: 'Tisza floods Szeged streets', language: 'en',
    text: 'strike', sentence: 'The strike is expected to last a week.', trigger: [4, 10],
    event_type: 'labor_action', significance: 0.4,
    decision: { outcome: 'join', adapter: 'threshold', deployment: null, noul: null, reason: null, probabilities: null, candidates: [{ cluster_id: CLUSTER_STRIKE, score: 0.83, label: null, field_scores: null }] }
  }
]

const singleMembers: Member[] = [
  {
    mention_id: 'm0000000-0000-4000-8000-000000000006', document_id: DOC_HI, document_title: 'तिस्ज़ा नदी में बाढ़', language: 'hi',
    text: 'बाढ़', sentence: 'तिस्ज़ा नदी में बाढ़ आ गई।', trigger: [14, 18], event_type: null, significance: null, decision: null
  }
]

export const membersByCluster: Record<string, Member[]> = {
  [CLUSTER_FLOOD]: floodMembers,
  [CLUSTER_STRIKE]: strikeMembers,
  [CLUSTER_SINGLE]: singleMembers
}
