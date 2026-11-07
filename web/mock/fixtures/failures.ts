import type { Ok } from './types'
import { RUN_LLM } from './ids'

type Failure = Ok<'/runs/{run_id}/failures', 'get'>['items'][number]

const ids = ['01', '02', '03', '04', '05', '06']
const errors: Array<string | null> = [
  'LLM response was not valid JSON after 3 attempts',
  'deployment claude-sonnet returned 529 overloaded',
  'LLM response was not valid JSON after 3 attempts',
  'context window exceeded: 9120 > 8192 tokens',
  null, // a failed job that recorded no error text
  'deployment claude-sonnet returned 529 overloaded'
]

export const llmFailures: Failure[] = ids.map((n, i) => ({
  stage: 'cluster' as const,
  unit_kind: 'mention' as const,
  unit_id: `m0000000-0000-4000-8000-0000000001${n}`,
  attempts: i === 3 ? 1 : 3,
  error: errors[i]!
}))
