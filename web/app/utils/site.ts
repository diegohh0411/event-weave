export function formatChangeReport(c: { stored: number, updated: number, removed: number, rejected?: number, errors?: number }) {
  return [[c.stored, 'new'], [c.updated, 'edited'], [c.removed, 'removed'], [c.rejected, 'rejected'], [c.errors, 'errors']].filter(([n]) => n).map(([n, label]) => `${n} ${label}`).join(' · ') || 'No changes'
}
function validDomain(input: string) {
  const value = input.trim().toLowerCase().replace(/\.$/, '')
  const labels = value.split('.')
  return value.length <= 253 && labels.length > 1 && !/^\d+$/.test(labels[labels.length - 1]!) && labels.every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
}
export function validateSite(s: { feeds?: string[], sitemaps?: string[], name?: string | null, domain: string, per_day: number | null, check_every_minutes: number, daily_cap: number, min_interval_seconds: number, max_parallel: number, backfill_mode?: 'off' | 'date' | 'all', backfill_from?: string | null, backfill_per_hour?: number }) {
  if (!s.name?.trim() || !s.domain.trim()) return 'Name and domain are required.'
  if (!validDomain(s.domain)) return 'Enter a valid domain.'
  for (const sources of [s.feeds ?? [], s.sitemaps ?? []]) {
    if (sources.length > 20) return 'Enter at most 20 URLs per source type.'
    for (const source of sources) {
      try {
        const url = new URL(source)
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port || !validDomain(url.hostname) || /\s/.test(source)) throw new Error()
      } catch { return 'Enter valid public HTTP(S) source URLs.' }
    }
  }
  if (s.per_day !== null && (!Number.isInteger(s.per_day) || s.per_day < 1 || s.per_day > 200)) return 'Articles per day must be between 1 and 200.'
  if (!Number.isInteger(s.check_every_minutes) || s.check_every_minutes < 5) return 'Check interval must be at least 5 minutes.'
  if (!Number.isInteger(s.daily_cap) || s.daily_cap < 1) return 'Daily cap must be at least 1.'
  if (!Number.isFinite(s.min_interval_seconds) || s.min_interval_seconds < 0.5) return 'Minimum interval must be at least 0.5 seconds.'
  if (!Number.isInteger(s.max_parallel) || s.max_parallel < 1 || s.max_parallel > 4) return 'Max parallel must be between 1 and 4.'
  if (s.backfill_mode === 'date' && !s.backfill_from) return 'A backfill date is required.'
  if (s.backfill_from && (Number.isNaN(Date.parse(s.backfill_from)) || s.backfill_from > new Date().toISOString().slice(0, 10))) return 'Backfill date cannot be invalid or in the future.'
  if (s.backfill_per_hour !== undefined && (!Number.isInteger(s.backfill_per_hour) || s.backfill_per_hour < 1 || s.backfill_per_hour > 1000)) return 'Backfill pace must be between 1 and 1000.'
  return ''
}
export function relativeTime(value: string) {
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const abs = Math.abs(seconds)
  const [amount, unit] = abs < 3600 ? [Math.round(seconds / 60), 'minute'] : abs < 86400 ? [Math.round(seconds / 3600), 'hour'] : [Math.round(seconds / 86400), 'day']
  return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(amount as number, unit as Intl.RelativeTimeFormatUnit)
}

export function backfillPercent(done: number, found: number) { return found ? Math.min  (100, Math.round(done / found * 100)) : 0 }
export function formatBackfillEta(hours: number | null) {
  if (hours === null) return 'Still scanning sitemaps'
  const minutes = Math.ceil(hours * 60)
  return `ETA: ${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
