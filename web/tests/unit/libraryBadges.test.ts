import { describe, expect, it } from 'vitest'
import { formatChangeBadges } from '../../app/utils/libraryBadges'

const doc = { versions: 1, removed_at: null as string | null }

describe('formatChangeBadges', () => {
  it('counts edits as versions minus one', () => {
    expect(formatChangeBadges({ ...doc, versions: 3 })).toEqual([{ label: 'edited 2×', color: 'warning' }])
  })
  it('shows the removal date', () => {
    expect(formatChangeBadges({ ...doc, removed_at: '2026-11-14T09:30:00Z' })).toEqual([{ label: 'removed Nov 14', color: 'error' }])
  })
  it('lists edited before removed', () => {
    expect(formatChangeBadges({ versions: 2, removed_at: '2026-11-14T00:00:00Z' }).map(b => b.label)).toEqual(['edited 1×', 'removed Nov 14'])
  })
  it('is empty for unchanged documents', () => {
    expect(formatChangeBadges(doc)).toEqual([])
  })
})
