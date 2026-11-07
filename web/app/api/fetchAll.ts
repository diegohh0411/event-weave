export interface PageLike<T> { items: T[], total: number }

/** Reads every page of a list endpoint (limit 200 is the API's maximum) until `items` reaches `total`. */
export async function fetchAll<T>(
  fetchPage: (offset: number, limit: number) => Promise<PageLike<T> | undefined>,
  limit = 200
): Promise<T[]> {
  const all: T[] = []
  for (;;) {
    const page = await fetchPage(all.length, limit)
    if (!page) throw new Error('could not load a page of results')
    all.push(...page.items)
    if (!page.items.length || all.length >= page.total) return all
  }
}
