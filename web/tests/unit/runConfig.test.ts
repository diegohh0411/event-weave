import { describe, expect, it } from 'vitest'
import { buildRunConfig, type BuilderState } from '../../app/utils/runConfig'
import fixture from '../fixtures/builder_run_config.json'

const state: BuilderState = {
  name: 'builder-fixture',
  track: 'paper',
  seed: 7,
  stages: {
    provide: { adapter: 'mcecr', settings: { languages: ['english'], splits: ['dev'] }, deployments: {} },
    extract: { adapter: 'gold', settings: {}, deployments: {} },
    tag: undefined,
    enrich: { adapter: 'clairic', settings: { window_sentences: 2 }, deployments: { extraction: 'stub-extraction' } },
    embed: { adapter: 'embedding-gemma', settings: { dimension: 8 }, deployments: { embeddings: 'stub-embeddings' } },
    cluster: {
      adapter: 'threshold',
      settings: { k: 5, threshold: 0.8, weights: { action: 0.4, participants: 0.3, location: 0.2, time: 0.1 } },
      deployments: {}
    }
  }
}

describe('buildRunConfig', () => {
  it('produces the JSON the Python hash test sends', () => {
    expect(JSON.parse(JSON.stringify(buildRunConfig(state)))).toEqual(fixture)
  })

  it('leaves version unset and skips stages with no adapter', () => {
    const cfg = buildRunConfig(state)
    expect(Object.keys(cfg.stages)).not.toContain('tag')
    for (const s of Object.values(cfg.stages)) expect(s).not.toHaveProperty('version')
  })

  it('omits settings left at their schema default', () => {
    const cfg = buildRunConfig({
      ...state,
      stages: { ...state.stages, cluster: { adapter: 'threshold', settings: { k: 5, threshold: 0.9 }, deployments: {}, defaults: { k: 5, threshold: 0.8 } } }
    })
    expect(cfg.stages.cluster!.settings).toEqual({ threshold: 0.9 })
  })
})
