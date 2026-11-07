export interface Segment { text: string, mark: boolean }

/**
 * Splits `text` into plain and marked slices. Ranges are `[start, end)` character offsets;
 * invalid or empty ranges are dropped and overlapping ones merged. The slices always join back to `text`.
 */
export function highlightSegments(text: string, ranges: readonly (readonly [number, number])[]): Segment[] {
  const valid = ranges
    .filter(([s, e]) => Number.isInteger(s) && Number.isInteger(e) && s >= 0 && e > s && s < text.length)
    .map(([s, e]) => [s, Math.min(e, text.length)] as [number, number])
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
    if (s > at) out.push({ text: text.slice(at, s), mark: false })
    out.push({ text: text.slice(s, e), mark: true })
    at = e
  }
  if (at < text.length || !out.length) out.push({ text: text.slice(at), mark: false })
  return out
}
