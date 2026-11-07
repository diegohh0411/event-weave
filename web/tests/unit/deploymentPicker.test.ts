import { describe, expect, it } from 'vitest'
import { readyDeploymentsFor } from '../../app/utils/runConfig'
import { deployments } from '../../mock/fixtures/deployments'

describe('readyDeploymentsFor', () => {
  it('keeps only ready deployments with the capability', () => {
    expect(readyDeploymentsFor(deployments.items, 'llm').map(d => d.id)).toEqual(['claude-sonnet'])
    expect(readyDeploymentsFor(deployments.items, 'embeddings').map(d => d.id)).toEqual(['stub-embeddings'])
  })
  it('is empty when nothing is ready', () => {
    expect(readyDeploymentsFor(deployments.items.filter(d => d.id !== 'claude-sonnet'), 'llm')).toEqual([])
  })
})
