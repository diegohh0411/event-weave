import { describe, expect, it } from 'vitest'
import { formatChangeReport, validateSite } from '../../app/utils/site'
describe('site helpers', () => {
  it('formats only changes', () => {
    expect(formatChangeReport({ stored: 14, updated: 2, removed: 1 })).toBe('14 new · 2 edited · 1 removed')
    expect(formatChangeReport({ stored: 0, updated: 0, removed: 0, rejected: 3, errors: 2 })).toBe('3 rejected · 2 errors')
    expect(formatChangeReport({ stored: 0, updated: 0, removed: 0 })).toBe('No changes')
  })
  it('checks scheduling and throttle ranges', () => {
    const valid = { name: 'News', domain: 'example.com', per_day: 3, check_every_minutes: 5, daily_cap: 1, min_interval_seconds: 0.5, max_parallel: 4 }
    expect(validateSite(valid)).toBe('')
    expect(validateSite({ ...valid, domain: 'invalid' })).toBeTruthy()
    expect(validateSite({ ...valid, feeds: ['ftp://example.com/feed'] })).toBeTruthy()
    for (const patch of [{ max_parallel: 5 }, { check_every_minutes: 4 }, { daily_cap: 0 }, { min_interval_seconds: 0.1 }, { per_day: 201 }]) expect(validateSite({ ...valid, ...patch })).toBeTruthy()
  })
})

describe('backfill helpers', () => {
  it('validates date and pace', () => {
    const base = { name: 'News', domain: 'example.com', per_day: 3, check_every_minutes: 5, daily_cap: 1, min_interval_seconds: 0.5, max_parallel: 1, backfill_mode: 'date' as const, backfill_per_hour: 60 }
    expect(validateSite(base)).toContain('date')
    expect(validateSite({ ...base, backfill_from: '2999-01-01' })).toContain('future')
    expect(validateSite({ ...base, backfill_from: '2020-01-01', backfill_per_hour: 1001 })).toContain('1 and 1000')
    expect(validateSite({ ...base, backfill_from: '2020-01-01' })).toBe('')
  })
  it('formats progress and ETA', async () => {
    const { backfillPercent, formatBackfillEta } = await import('../../app/utils/site')
    expect(backfillPercent(1, 4)).toBe(25)
    expect(backfillPercent(0, 0)).toBe(0)
    expect(formatBackfillEta(null)).toBe('Still scanning sitemaps')
    expect(formatBackfillEta(1.5)).toBe('ETA: 1h 30m')
  })
})
