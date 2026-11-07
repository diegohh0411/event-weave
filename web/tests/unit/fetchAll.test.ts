import { describe, expect, it } from 'vitest'
import { fetchAll } from '../../app/api/fetchAll'

describe('fetchAll', () => {
  it('reads every page when total is above one page', async () => {
    const rows = Array.from({ length: 450 }, (_, i) => i)
    const asked: [number, number][] = []
    const got = await fetchAll(async (offset, limit) => {
      asked.push([offset, limit])
      return { items: rows.slice(offset, offset + limit), total: rows.length }
    })
    expect(got).toEqual(rows)
    expect(asked).toEqual([[0, 200], [200, 200], [400, 200]])
  })
  it('stops on an empty page and throws on a failed one', async () => {
    expect(await fetchAll(async () => ({ items: [], total: 5 }))).toEqual([])
    await expect(fetchAll(async () => undefined)).rejects.toThrow()
  })
})
