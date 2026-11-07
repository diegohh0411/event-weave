import { describe, expect, it } from 'vitest'
import { highlightSegments } from '../../app/utils/highlight'
import { membersByCluster } from '../../mock/fixtures/clusters'

const join = (s: { text: string, mark: boolean }[]) => s.map(x => x.text).join('')

describe('highlightSegments', () => {
  it('highlight_marks_exact_slice', () => {
    const s = highlightSegments('The river Tisza flooded the streets.', [[16, 23]])
    expect(s).toEqual([
      { text: 'The river Tisza ', mark: false },
      { text: 'flooded', mark: true },
      { text: ' the streets.', mark: false }
    ])
  })

  it('orders ranges, merges overlaps and drops invalid ones', () => {
    const s = highlightSegments('abcdefgh', [[4, 6], [0, 2], [1, 3], [10, 12], [5, 5], [-1, 2]])
    expect(s.filter(x => x.mark).map(x => x.text)).toEqual(['abc', 'ef'])
    expect(join(s)).toBe('abcdefgh')
  })

  it('keeps text that looks like HTML as plain text', () => {
    const s = highlightSegments('<b>x</b> hit', [[9, 12]])
    expect(s.filter(x => x.mark).map(x => x.text)).toEqual(['hit'])
    expect(join(s)).toBe('<b>x</b> hit')
  })

  it('returns the text unmarked with no ranges', () => {
    expect(highlightSegments('abc', [])).toEqual([{ text: 'abc', mark: false }])
  })
})

describe('member fixtures', () => {
  it('have trigger offsets that slice the sentence to the mention text', () => {
    for (const m of Object.values(membersByCluster).flat()) expect(m.sentence.slice(m.trigger[0], m.trigger[1])).toBe(m.text)
  })
})
