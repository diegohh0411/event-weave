import type { Ok } from './types'

type Adapters = Ok<'/adapters', 'get'>

const obj = (properties: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  ({ type: 'object', properties, additionalProperties: false, ...extra })

export const adapters: Adapters = {
  items: [
    {
      name: 'mcecr', version: '1', stage: 'provide', input_kind: 'run', capabilities: [], cacheable: false, optional_stage: false,
      settings_schema: obj({
        languages: { type: 'array', items: { enum: ['english', 'spanish', 'turkish', 'hindi'] }, default: ['english'], title: 'Languages' },
        splits: { type: 'array', items: { enum: ['train', 'dev', 'test'] }, default: ['dev'], title: 'Splits' }
      })
    },
    {
      name: 'gold', version: '1', stage: 'extract', input_kind: 'document', capabilities: [], cacheable: false, optional_stage: false,
      settings_schema: obj({})
    },
    {
      name: 'stanza-srl', version: '1', stage: 'extract', input_kind: 'document', capabilities: [], cacheable: true, optional_stage: false,
      settings_schema: obj({ min_mention_chars: { type: 'integer', minimum: 1, default: 3, title: 'Minimum mention length' } })
    },
    {
      name: 'clairic', version: '1', stage: 'enrich', input_kind: 'mention', capabilities: ['extraction'], cacheable: true, optional_stage: true,
      settings_schema: obj({ window_sentences: { type: 'integer', minimum: 0, maximum: 8, default: 2, title: 'Window sentences' } })
    },
    {
      name: 'embedding-gemma', version: '1', stage: 'embed', input_kind: 'mention', capabilities: ['embeddings'], cacheable: true, optional_stage: false,
      settings_schema: obj({ dimension: { type: 'integer', enum: [8, 128, 256, 768], default: 256, title: 'Dimension' } })
    },
    {
      name: 'threshold', version: '1', stage: 'cluster', input_kind: 'mention', capabilities: [], cacheable: false, optional_stage: false,
      settings_schema: obj({
        k: { type: 'integer', minimum: 1, default: 5, title: 'Neighbours' },
        threshold: { type: 'number', minimum: 0, maximum: 1, default: 0.8, title: 'Threshold' },
        weights: obj({
          action: { type: 'number', default: 0.4 },
          participants: { type: 'number', default: 0.3 },
          location: { type: 'number', default: 0.2 },
          time: { type: 'number', default: 0.1 }
        }, { title: 'Field weights' })
      })
    },
    {
      name: 'llm-judge', version: '1', stage: 'cluster', input_kind: 'mention', capabilities: ['embeddings', 'llm'], cacheable: true, optional_stage: false,
      settings_schema: obj({
        k: { type: 'integer', minimum: 1, default: 5, title: 'Candidates shown' },
        temperature: { type: 'number', minimum: 0, maximum: 2, default: 0, title: 'Temperature' },
        instructions: { type: 'string', default: '', title: 'Extra instructions' }
      })
    }
  ],
  total: 7,
  offset: 0,
  limit: 50
}
