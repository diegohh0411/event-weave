export function apiError(error: unknown): string {
  const e = error as { detail?: unknown, problems?: string[] } | undefined
  if (Array.isArray(e?.detail)) return e.detail.map((d: { loc?: unknown[], msg?: string }) => `${(d.loc ?? []).join('.')}: ${d.msg ?? 'Invalid value'}`).join('; ')
  if (typeof e?.detail === 'string') return e.detail
  if (e?.problems?.length) return e.problems.join('; ')
  return 'The API rejected the request.'
}
