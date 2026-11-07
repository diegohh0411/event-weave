import type { Ok, Schemas } from './types'
import { RUN_CANCELLED, RUN_LLM, RUN_NEW, RUN_PAPER } from './ids'

type RunOut = Ok<'/runs/{run_id}', 'get'>
type RunConfig = Schemas['RunConfig']

const paperConfig: RunConfig = {
  name: 'paper-baseline',
  track: 'paper',
  seed: 7,
  stages: {
    provide: { adapter: 'mcecr', settings: { languages: ['english', 'spanish', 'hindi'], splits: ['test'] }, deployments: {} },
    extract: { adapter: 'gold', settings: {}, deployments: {} },
    enrich: { adapter: 'clairic', settings: { window_sentences: 2 }, deployments: { extraction: 'stub-extraction' } },
    embed: { adapter: 'embedding-gemma', settings: { dimension: 256 }, deployments: { embeddings: 'stub-embeddings' } },
    cluster: { adapter: 'threshold', settings: { k: 5, threshold: 0.8, weights: { action: 0.4, participants: 0.3, location: 0.2, time: 0.1 } }, deployments: {} }
  }
}

const llmConfig: RunConfig = {
  name: 'realistic-llm-judge',
  track: 'realistic',
  seed: 11,
  stages: {
    provide: { adapter: 'mcecr', settings: { languages: ['english'], splits: ['test'] }, deployments: {} },
    extract: { adapter: 'stanza-srl', settings: { min_mention_chars: 3 }, deployments: {} },
    embed: { adapter: 'embedding-gemma', settings: { dimension: 256 }, deployments: { embeddings: 'stub-embeddings' } },
    cluster: { adapter: 'llm-judge', settings: { k: 5, temperature: 0 }, deployments: { embeddings: 'stub-embeddings', llm: 'claude-sonnet' } }
  }
}

const progress = (queued: number, running: number, done: number, failed: number) => ({ queued, running, done, failed })

export const runPaper: RunOut = {
  id: RUN_PAPER,
  name: paperConfig.name,
  track: 'paper',
  state: 'done',
  config: paperConfig,
  config_hash: 'a3f5c9d17e2b4a60',
  created_at: '2026-11-05T09:12:03Z',
  started_at: '2026-11-05T09:12:41Z',
  finished_at: '2026-11-05T10:41:12Z',
  dollars: '0.0000',
  dollars_estimated: false,
  mentions_per_hour: 4210.5,
  progress: {
    provide: progress(0, 0, 1, 0),
    extract: progress(0, 0, 412, 0),
    enrich: progress(0, 0, 6230, 0),
    embed: progress(0, 0, 6230, 0),
    cluster: progress(0, 0, 6230, 0)
  }
}

// Running, with failed cluster jobs: those mentions stay unclustered and the runs page lists each error.
export const runLlm: RunOut = {
  id: RUN_LLM,
  name: llmConfig.name,
  track: 'realistic',
  state: 'running',
  config: llmConfig,
  config_hash: '5e0b81c4d9aa7f13',
  created_at: '2026-11-06T14:00:00Z',
  started_at: '2026-11-06T14:01:10Z',
  finished_at: null,
  dollars: '3.4172',
  dollars_estimated: true,
  mentions_per_hour: 912.25,
  progress: {
    provide: progress(0, 0, 1, 0),
    extract: progress(0, 0, 412, 0),
    embed: progress(0, 0, 6230, 0),
    cluster: progress(3100, 4, 3120, 6)
  }
}

export const runNew: RunOut = {
  id: RUN_NEW,
  name: 'paper-rerun-seed-8',
  track: 'paper',
  state: 'created',
  config: { ...paperConfig, name: 'paper-rerun-seed-8', seed: 8 },
  config_hash: '77d2e0a4b6c81f95',
  created_at: '2026-11-07T08:30:00Z',
  started_at: null,
  finished_at: null,
  dollars: '0.0000',
  dollars_estimated: false,
  mentions_per_hour: null,
  progress: {}
}

export const runCancelled: RunOut = {
  id: RUN_CANCELLED,
  name: 'realistic-cancelled',
  track: 'realistic',
  state: 'cancelled',
  config: { ...llmConfig, name: 'realistic-cancelled', seed: 12 },
  config_hash: '19c4f3a8e5d07b62',
  created_at: '2026-11-04T16:45:00Z',
  started_at: '2026-11-04T16:46:00Z',
  finished_at: '2026-11-04T17:02:30Z',
  dollars: '0.8120',
  dollars_estimated: true,
  mentions_per_hour: 640,
  progress: {
    provide: progress(0, 0, 1, 0),
    extract: progress(300, 0, 112, 0)
  }
}

export const allRuns: RunOut[] = [runLlm, runPaper, runNew, runCancelled]

export const summary = (r: RunOut) => ({
  id: r.id, name: r.name, track: r.track, state: r.state, config_hash: r.config_hash,
  created_at: r.created_at, started_at: r.started_at, finished_at: r.finished_at
})

export const estimates = {
  priced: { amount: '4.8300', cached: { extract: { cached: 0, total: 412 }, cluster: { cached: 1800, total: 6230 } }, reason: null },
  // Never rendered as $0: the page shows "No estimate yet: <reason>".
  unpriced: { amount: null, cached: null, reason: 'deployment local-qwen has no price per token' }
} satisfies Record<string, Ok<'/runs/estimate', 'post'>>
