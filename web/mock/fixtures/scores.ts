import type { Ok } from './types'
import { EVALUATION, RUN_LLM, RUN_NEW, RUN_PAPER } from './ids'

type ScoresOut = Ok<'/runs/{run_id}/scores', 'get'>
type Score = ScoresOut['scores'][number]
type Compare = Ok<'/compare', 'get'>
type Row = NonNullable<Compare['paper']>[number]

const prf = (f1: number, precision: number | null = f1 + 0.02, recall: number | null = f1 - 0.02) => ({ f1, precision, recall })

const score = (language: string, setting: string, metric: string, f1: number, ci = true): Score => ({
  track: 'paper', setting, language, metric, f1, precision: f1 + 0.02, recall: f1 - 0.02,
  f1_low: ci ? f1 - 0.03 : null, f1_high: ci ? f1 + 0.03 : null
})

// Hindi has no cross-document score: the Track 1 scorer skips it (see SKIPPED in weave_eval.tracks).
export const paperScores: Score[] = [
  score('en', 'within', 'muc', 0.71), score('en', 'within', 'conll', 0.64),
  score('en', 'cross', 'muc', 0.58), score('en', 'cross', 'conll', 0.52),
  score('es', 'within', 'muc', 0.66), score('es', 'within', 'conll', 0.6),
  score('es', 'cross', 'muc', 0.49, false), score('es', 'cross', 'conll', 0.44, false),
  score('hi', 'within', 'muc', 0.55), score('hi', 'within', 'conll', 0.5)
]

export const scoresPaper: ScoresOut = {
  evaluation_id: EVALUATION,
  split: 'test',
  track: 'paper',
  scorer_version: '1',
  created_at: '2026-11-05T10:55:00Z',
  scores: paperScores,
  report: { scorer_version: '1', track: 'paper', triggers: null }
}

export const HINDI_CROSS_SKIPPED = "not scored: the dataset's Hindi crossDoc pairs have no topic or mention ids"
const skipped = { 'hi/cross': HINDI_CROSS_SKIPPED }

const cost = (total: string, perK: string | null, rate: number | null, estimated: boolean) =>
  ({ total_dollars: total, dollars_per_1000_mentions: perK, mentions_per_hour: rate, any_estimated: estimated })

const paperRun: Compare['runs'][number] = {
  run_id: RUN_PAPER, name: 'paper-baseline', track: 'paper', evaluated: true, scores: paperScores, trigger: null,
  cost: cost('0.0000', '0.0000', 4210.5, false), failures: {}, skipped
}

const llmRun: Compare['runs'][number] = {
  run_id: RUN_LLM, name: 'realistic-llm-judge', track: 'realistic', evaluated: true,
  scores: [
    { track: 'realistic', setting: 'pooled', language: 'en', metric: 'muc', f1: 0.62, precision: 0.7, recall: 0.56, f1_low: 0.58, f1_high: 0.66 }
  ],
  trigger: { f1: 0.81, precision: 0.84, recall: 0.78 },
  cost: cost('3.4172', '0.5485', 912.25, true),
  failures: { cluster: { 'mention': 6 } },
  skipped
}

// Paper numbers are printed as PRF triples (tables 2-3) or F1 only (tables 4-7).
const paperRows: Row[] = [
  {
    language: 'en', setting: 'within', metric: 'muc', regime: 'monolingual', label: 'our setting is harder (we are tuned on English dev only)',
    ours: prf(0.71), ours_ci: [0.68, 0.74], skipped: null,
    paper: { hierarchical: prf(0.74, 0.77, 0.72), joint: prf(0.69, 0.7, 0.68) }
  },
  {
    language: 'en', setting: 'cross', metric: 'conll', regime: 'monolingual', label: 'our setting is harder (we are tuned on English dev only)',
    ours: prf(0.52), ours_ci: [0.49, 0.55], skipped: null,
    paper: { hierarchical: 0.55 }
  },
  {
    language: 'es', setting: 'within', metric: 'muc', regime: 'transfer', label: 'like for like (both tuned on English only)',
    ours: prf(0.66), ours_ci: [0.63, 0.69], skipped: null,
    paper: { xlmr: 0.61, synonym: 0.64 }
  },
  {
    language: 'hi', setting: 'cross', metric: 'conll', regime: 'transfer', label: 'like for like (both tuned on English only)',
    ours: null, ours_ci: null, skipped: HINDI_CROSS_SKIPPED,
    paper: { xlmr: 0.47 }
  },
  {
    language: 'hi', setting: 'cross', metric: 'muc', regime: 'monolingual', label: 'our setting is harder (we are tuned on English dev only)',
    ours: null, ours_ci: null, skipped: HINDI_CROSS_SKIPPED,
    paper: {}
  }
]

const paperSource = {
  citation: 'Pouran Ben Veyseh et al., MCECR: A Novel Dataset for Multilingual Cross-Document Event Coreference Resolution. Findings of NAACL 2024.',
  url: 'https://aclanthology.org/2024.findings-naacl.245.pdf',
  anthology_id: '2024.findings-naacl.245'
}

export const compareDefault: Compare = {
  runs: [paperRun, llmRun],
  paper: paperRows,
  paper_checked: false, // the page shows the "Paper numbers UNCHECKED" banner
  paper_source: paperSource
}

// Realistic only: no Track 1 run, so no paper rows at all.
export const compareRealistic: Compare = {
  runs: [llmRun],
  paper: null,
  paper_checked: true,
  paper_source: paperSource
}

// A Track 1 run nobody has evaluated: "Not evaluated (run weave eval <id>)", never zeros.
export const compareUnevaluated: Compare = {
  runs: [{
    run_id: RUN_NEW, name: 'paper-rerun-seed-8', track: 'paper', evaluated: false, scores: [], trigger: null,
    cost: cost('0.0000', null, null, false), failures: {}, skipped
  }],
  paper: paperRows.map(r => ({ ...r, ours: null, ours_ci: null })),
  paper_checked: true,
  paper_source: paperSource
}
