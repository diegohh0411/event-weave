import type { paths } from '../../app/api/schema'

type Methods = 'get' | 'post'

/** The JSON body of a route's success response, taken from the generated schema. */
export type Ok<P extends keyof paths, M extends Methods, S extends number = 200> =
  paths[P] extends Record<M, { responses: Record<S, { content: { 'application/json': infer B } }> }> ? B : never

/** The JSON request body of a route. */
export type Body<P extends keyof paths, M extends Methods> =
  paths[P] extends Record<M, { requestBody: { content: { 'application/json': infer B } } }> ? B : never

export type Schemas = import('../../app/api/schema').components['schemas']

/**
 * One example response for a route. `params` narrows it to requests that carry those path or
 * query values (a fixture with no `params` is the default for the route). Bodies of list routes are
 * the full, unpaged page: the mock server slices `items` with the request's offset and limit.
 */
export interface Fixture<B = unknown> {
  status: 200 | 201 | 202
  body: B
  params?: Record<string, string>
  /** Extra condition on the request, for fixtures chosen by body or by a repeated query key. */
  match?: (req: { body: unknown, query: URLSearchParams }) => boolean
}

export const ok = <B>(body: B, params?: Record<string, string>): Fixture<B> => ({ status: 200, body, params })
export const created = <B>(body: B): Fixture<B> => ({ status: 201, body })

/** Wrap items as the API's `Page[T]` (one page holding everything). */
export const page = <T>(items: T[]) => ({ items, total: items.length, offset: 0, limit: 50 })
