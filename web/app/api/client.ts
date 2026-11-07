import createClient from 'openapi-fetch'
import type { paths } from './schema'

/** Typed client for the weave API. The base URL comes from NUXT_PUBLIC_API_BASE. */
export function useApi() {
  return createClient<paths>({ baseUrl: useRuntimeConfig().public.apiBase })
}
