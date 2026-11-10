export function exclusiveEndOfDate(date: string): string | undefined {
  if (!date) return undefined
  const end = new Date(`${date}T00:00:00Z`)
  end.setUTCDate(end.getUTCDate() + 1)
  return end.toISOString()
}
