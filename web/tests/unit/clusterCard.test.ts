import { describe, expect, it } from 'vitest'
import { cardFields } from '../../app/utils/clusterCard'
import { clusterSummaries } from '../../mock/fixtures/clusters'

describe('cardFields', () => {
  it('reads the four fields from card.values (the production Card.canonical shape)', () => {
    const card = clusterSummaries[0]!.card!
    expect(Object.keys(card).sort()).toEqual(['earliest', 'languages', 'latest', 'sentence', 'size', 'values'])
    expect(cardFields(card)).toEqual({ action: 'flooded', participants: 'River Tisza, Szeged', location: 'Szeged, Hungary', time: '2026-03-14' })
  })
  it('is empty for a missing card or missing values', () => {
    expect(cardFields(null)).toEqual({ action: '', participants: '', location: '', time: '' })
    expect(cardFields({ size: 1 }).action).toBe('')
  })
})
