import { describe, expect, it } from 'vitest'
import { formatChangeReport, validateSite } from '../../app/utils/site'
describe('site helpers', () => {
  it('formats only changes', () => {
    expect(formatChangeReport({ stored: 14, updated: 2, removed: 1 })).toBe('14 new · 2 edited · 1 removed')
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
