import { describe, expect, it } from 'vitest'
import { clusterColors, PALETTE } from '../../app/utils/clusterColors'

describe('clusterColors', () => {
  it('same_colour_iff_same_cluster', () => {
    // 40 distinct ids, each appearing several times in a scrambled order, with nulls mixed in.
    const ids = Array.from({ length: 40 }, (_, i) => `cluster-${i}`)
    const seq: (string | null)[] = []
    for (let r = 0; r < 3; r++) for (let i = 0; i < ids.length; i++) seq.push(ids[(i * (7 + r * 4)) % ids.length]!, null)
    const colours = clusterColors(seq)
    expect(colours.size).toBe(40)
    expect(new Set(colours.values()).size).toBe(40)
    for (const a of ids) for (const b of ids) expect(colours.get(a) === colours.get(b)).toBe(a === b)
  })

  it('assigns by first appearance: palette first, then golden-angle hsl', () => {
    const ids = Array.from({ length: 14 }, (_, i) => `c${i}`)
    const colours = clusterColors([...ids, ...ids])
    ids.slice(0, 12).forEach((id, i) => expect(colours.get(id)).toBe(PALETTE[i]))
    expect(PALETTE.length).toBe(12)
    expect(colours.get('c12')).toMatch(/^hsl\(\d+(\.\d+)?, 65%, 65%\)$/)
    expect(colours.get('c13')).toMatch(/^hsl\(\d+(\.\d+)?, 65%, 45%\)$/)
  })

  it('null_cluster_gets_no_fill', () => {
    const colours = clusterColors(['a', null, 'b', null])
    expect([...colours.keys()]).toEqual(['a', 'b'])
    expect(colours.has(null as unknown as string)).toBe(false)
    expect(clusterColors([null, null]).size).toBe(0)
  })
})
