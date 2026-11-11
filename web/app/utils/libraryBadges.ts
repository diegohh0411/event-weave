export type ChangeBadge = { label: string, color: 'warning' | 'error' }

export function formatChangeBadges(doc: { versions: number, removed_at?: string | null }): ChangeBadge[] {
  const badges: ChangeBadge[] = []
  if (doc.versions > 1) badges.push({ label: `edited ${doc.versions - 1}×`, color: 'warning' })
  if (doc.removed_at) {
    const day = new Date(doc.removed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
    badges.push({ label: `removed ${day}`, color: 'error' })
  }
  return badges
}
