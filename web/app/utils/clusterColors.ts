/** The 12 Nuxt UI palette colours (Tailwind 500 shades), used for the first 12 clusters. */
export const PALETTE = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16', '#22c55e',
  '#14b8a6', '#06b6d4', '#0ea5e9', '#3b82f6', '#8b5cf6', '#ec4899'
] as const

const GOLDEN_ANGLE = 137.508

/**
 * One distinct colour per distinct non-null cluster id, in order of first appearance.
 * A null id gets no entry, so it has no fill. Throws if a colour would be used twice.
 */
export function clusterColors(clusterIds: readonly (string | null)[]): Map<string, string> {
  const out = new Map<string, string>()
  const used = new Set<string>()
  for (const id of clusterIds) {
    if (id === null || out.has(id)) continue
    const i = out.size
    const colour = i < PALETTE.length
      ? PALETTE[i]!
      : `hsl(${Math.round(((i * GOLDEN_ANGLE) % 360) * 100) / 100}, 65%, ${i % 2 === 0 ? 65 : 45}%)`
    if (used.has(colour)) throw new Error(`cluster colour ${colour} would be used twice`)
    used.add(colour)
    out.set(id, colour)
  }
  return out
}
