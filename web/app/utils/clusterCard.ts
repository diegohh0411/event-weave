export interface CardFields { action: string, participants: string, location: string, time: string }

const FIELDS = ['action', 'participants', 'location', 'time'] as const

/** The four card fields as display text. The API keeps them as string lists under `card.values`. */
export function cardFields(card: Record<string, unknown> | null | undefined): CardFields {
  const values = (card?.values ?? {}) as Record<string, unknown>
  const out = {} as CardFields
  for (const f of FIELDS) {
    const v = values[f]
    out[f] = (Array.isArray(v) ? v.filter(x => typeof x === 'string' && x) : []).join(', ')
  }
  return out
}
