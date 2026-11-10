import { describe, expect, it } from 'vitest'
import { exclusiveEndOfDate } from '../../app/utils/inclusiveDate'

describe('inclusive published-to date', () => {
  it('uses the next UTC midnight, including across month and year boundaries', () => {
    expect(exclusiveEndOfDate('2024-02-29')).toBe('2024-03-01T00:00:00.000Z')
    expect(exclusiveEndOfDate('2024-12-31')).toBe('2025-01-01T00:00:00.000Z')
    expect(exclusiveEndOfDate('')).toBeUndefined()
  })
})
