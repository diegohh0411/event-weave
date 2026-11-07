export interface Segment { text: string, mark: boolean }

/** Slices by Unicode code points, the unit of the API's (Python) offsets, not UTF-16 units. */
export function sliceCodePoints(text: string, start: number, end?: number): string {
  return Array.from(text).slice(start, end).join('')
}

/**
 * Splits `text` into plain and marked slices. Ranges are `[start, end)` code-point offsets;
 * invalid or empty ranges are dropped and overlapping ones merged. The slices always join back to `text`.
 */
export function highlightSegments(text: string, ranges: readonly (readonly [number, number])[]): Segment[] {
  const chars = Array.from(text)
  const part = (s: number, e: number) => chars.slice(s, e).join('')
  const valid = ranges
    .filter(([s, e]) => Number.isInteger(s) && Number.isInteger(e) && s >= 0 && e > s && s < chars.length)
    .map(([s, e]) => [s, Math.min(e, chars.length)] as [number, number])
    .sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const r of valid) {
    const last = merged.at(-1)
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1])
    else merged.push(r)
  }
  const out: Segment[] = []
  let at = 0
  for (const [s, e] of merged) {
    if (s > at) out.push({ text: part(at, s), mark: false })
    out.push({ text: part(s, e), mark: true })
    at = e
  }
  if (at < chars.length || !out.length) out.push({ text: part(at, chars.length), mark: false })
  return out
}
