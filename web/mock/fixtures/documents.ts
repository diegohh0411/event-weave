import type { Ok } from './types'
import { DOC_EN, DOC_ES, DOC_HI, CLUSTER_FLOOD, CLUSTER_STRIKE } from './ids'

type Doc = Ok<'/runs/{run_id}/documents/{document_id}', 'get'>

const enText = 'The river Tisza flooded the lower streets of Szeged on Friday. Hundreds of residents were evacuated before dawn. Meanwhile, dockworkers in Rotterdam walked out over pay. The strike is expected to last a week. Officials said a sandbag wall was holding.'

const at = (text: string, needle: string) => {
  const start = text.indexOf(needle)
  if (start < 0) throw new Error(`fixture text lacks "${needle}"`)
  return { start, end: start + needle.length }
}

export const docEn: Doc = {
  id: DOC_EN,
  title: 'Tisza floods Szeged streets',
  outlet: 'Danube Daily',
  byline: 'A. Kovacs',
  language: 'en',
  url: 'https://example.test/danube-daily/tisza-floods-szeged',
  published_at: '2026-03-14T07:30:00Z',
  text: enText,
  mentions: [
    { id: 'm0000000-0000-4000-8000-000000000001', ...at(enText, 'flooded'), cluster_id: CLUSTER_FLOOD, cluster_size: 3, enrichment: { event_type: 'natural_disaster', significance: 0.92 } },
    { id: 'm0000000-0000-4000-8000-000000000002', ...at(enText, 'evacuated'), cluster_id: CLUSTER_FLOOD, cluster_size: 3, enrichment: { event_type: 'natural_disaster', significance: 0.74 } },
    { id: 'm0000000-0000-4000-8000-000000000004', ...at(enText, 'walked out'), cluster_id: CLUSTER_STRIKE, cluster_size: 2, enrichment: { event_type: 'labor_action', significance: 0.61 } },
    { id: 'm0000000-0000-4000-8000-000000000005', ...at(enText, 'strike'), cluster_id: CLUSTER_STRIKE, cluster_size: 2, enrichment: { event_type: 'labor_action', significance: 0.4 } },
    // Its cluster job failed: no fill in the document view, only a dashed underline.
    { id: 'm0000000-0000-4000-8000-000000000007', ...at(enText, 'holding'), cluster_id: null, cluster_size: null, enrichment: null }
  ]
}

const esText = 'El río Tisza inundó las calles bajas de Szeged el viernes.'
export const docEs: Doc = {
  id: DOC_ES,
  title: 'El Tisza inunda Szeged',
  outlet: 'Diario del Danubio',
  byline: '',
  language: 'es',
  url: 'https://example.test/diario-del-danubio/tisza-inunda-szeged',
  published_at: '2026-03-15T10:00:00Z',
  text: esText,
  mentions: [
    { id: 'm0000000-0000-4000-8000-000000000003', ...at(esText, 'inundó'), cluster_id: CLUSTER_FLOOD, cluster_size: 3, enrichment: null }
  ]
}

const hiText = 'तिस्ज़ा नदी में बाढ़ आ गई।'
export const docHi: Doc = {
  id: DOC_HI,
  title: 'तिस्ज़ा नदी में बाढ़',
  outlet: 'दैनिक डेन्यूब',
  byline: '',
  language: 'hi',
  url: 'https://example.test/dainik-danube/tisza-badh',
  published_at: null,
  text: hiText,
  mentions: [
    { id: 'm0000000-0000-4000-8000-000000000006', ...at(hiText, 'बाढ़'), cluster_id: 'c1000000-0000-4000-8000-000000000003', cluster_size: 1, enrichment: null }
  ]
}

export const allDocs: Doc[] = [docEn, docEs, docHi]

export const documentSummaries = allDocs.map(d => ({
  id: d.id, title: d.title, outlet: d.outlet, language: d.language, published_at: d.published_at, mentions: d.mentions.length
}))
