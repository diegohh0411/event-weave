import type { Ok } from './types'

type Deployments = Ok<'/deployments', 'get'>

export const deployments: Deployments = {
  items: [
    { id: 'stub-extraction', capability: 'extraction', ready: true, reason: null, local: true, is_default: true },
    { id: 'stub-embeddings', capability: 'embeddings', ready: true, reason: null, local: true, is_default: true },
    { id: 'claude-sonnet', capability: 'llm', ready: true, reason: null, local: false, is_default: true },
    // The not-ready case: the picker shows the reason and disables Launch.
    { id: 'local-qwen', capability: 'llm', ready: false, reason: 'connection refused at http://127.0.0.1:11434', local: true, is_default: false },
    { id: 'bge-m3', capability: 'system_one', ready: true, reason: null, local: true, is_default: true }
  ],
  total: 5,
  offset: 0,
  limit: 50
}
