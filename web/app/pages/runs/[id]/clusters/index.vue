<script setup lang="ts">
import { useApi } from '../../../../api/client'
import type { components } from '../../../../api/schema'
import { LANGUAGES } from '../../../../utils/languages'

type Summary = components['schemas']['ClusterSummary']

const PER_PAGE = 20
const api = useApi()
const route = useRoute()
const router = useRouter()
const id = String(route.params.id)

const one = (v: unknown) => (Array.isArray(v) ? v[0] : v) as string | undefined
const q = (k: string) => one(route.query[k]) ?? ''

// The URL query is the source of truth; every control reads and writes it.
const params = computed(() => ({
  q: q('q'), language: q('language'), min_size: q('min_size'), max_size: q('max_size'),
  event_type: q('event_type'), min_significance: q('min_significance'), sort: q('sort') || 'size',
  page: Math.max(1, Number(q('page')) || 1)
}))

function setQuery(patch: Record<string, string | number | undefined>, resetPage = true) {
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries({ ...route.query, ...(resetPage ? { page: undefined } : {}), ...patch })) {
    const s = one(v)
    if (s !== undefined && s !== '' && !(k === 'page' && s === '1')) next[k] = String(s)
  }
  return router.replace({ query: next })
}

const search = ref(q('q'))
let debounce: ReturnType<typeof setTimeout> | undefined
watch(() => q('q'), (v) => { if (v !== search.value.trim()) search.value = v })
watch(search, (v) => {
  if (v.trim() === q('q')) return
  clearTimeout(debounce)
  debounce = setTimeout(() => setQuery({ q: v.trim() }), 300)
})
onBeforeUnmount(() => clearTimeout(debounce))

const items = ref<Summary[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref<string>()
let seq = 0

async function load() {
  const mine = ++seq
  const p = params.value
  loading.value = true
  try {
    const r = await api.GET('/runs/{run_id}/clusters', {
      params: {
        path: { run_id: id },
        query: {
          q: p.q || undefined, language: p.language || undefined,
          min_size: p.min_size === '' ? undefined : Number(p.min_size),
          max_size: p.max_size === '' ? undefined : Number(p.max_size),
          event_type: p.event_type || undefined,
          min_significance: p.min_significance === '' ? undefined : Number(p.min_significance),
          sort: p.sort === 'recent' ? 'recent' : 'size',
          offset: (p.page - 1) * PER_PAGE, limit: PER_PAGE
        }
      }
    })
    if (mine !== seq) return // a newer filter change replaced this request
    if (!r.data) {
      const detail = (r.error as { detail?: unknown } | undefined)?.detail
      throw new Error(typeof detail === 'string' ? detail : 'the API rejected the request')
    }
    items.value = r.data.items
    total.value = r.data.total
    error.value = undefined
  } catch (e) {
    if (mine !== seq) return
    items.value = []
    total.value = 0
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}
watch(() => route.fullPath, load, { immediate: true })

const field = 'w-full rounded-md border border-default bg-default px-2 py-1.5 text-sm'
const cardLine = (c: Summary['card']) => (c ? [c.action, (c.participants as string[] | undefined)?.join(', '), c.location, c.time].filter(Boolean).join(' · ') : 'No card')
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-xl font-semibold">
      Clusters
    </h1>
    <form class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" @submit.prevent>
      <UInput v-model="search" data-test="search" class="sm:col-span-2" placeholder="Search clusters" aria-label="Search clusters" />
      <select data-test="language" :class="field" aria-label="Language" :value="params.language" @change="setQuery({ language: ($event.target as HTMLSelectElement).value })">
        <option value="">
          Any language
        </option>
        <option v-for="l in LANGUAGES" :key="l" :value="l">
          {{ l }}
        </option>
      </select>
      <select data-test="sort" :class="field" aria-label="Sort" :value="params.sort" @change="setQuery({ sort: ($event.target as HTMLSelectElement).value })">
        <option value="size">
          Largest first
        </option>
        <option value="recent">
          Most recent
        </option>
      </select>
      <UInput data-test="min-size" type="number" min="0" placeholder="Min size" aria-label="Minimum size" :model-value="params.min_size" @update:model-value="setQuery({ min_size: String($event) })" />
      <UInput data-test="max-size" type="number" min="0" placeholder="Max size" aria-label="Maximum size" :model-value="params.max_size" @update:model-value="setQuery({ max_size: String($event) })" />
      <UInput data-test="event-type" placeholder="Event type" aria-label="Event type" :model-value="params.event_type" @update:model-value="setQuery({ event_type: String($event) })" />
      <UInput data-test="min-significance" type="number" min="0" max="1" step="0.05" placeholder="Min significance" aria-label="Minimum significance" :model-value="params.min_significance" @update:model-value="setQuery({ min_significance: String($event) })" />
    </form>

    <UAlert v-if="error" color="error" variant="subtle" title="Could not load clusters" :description="error" />
    <p v-else-if="loading && !items.length" class="text-muted">
      Loading clusters...
    </p>
    <p v-else-if="!items.length" class="text-muted">
      No clusters match these filters.
    </p>
    <ul v-else class="space-y-3">
      <li v-for="c in items" :key="c.id">
        <UCard>
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <NuxtLink :to="`/runs/${id}/clusters/${c.id}`" class="min-w-0 break-words font-medium text-primary hover:underline">
              {{ cardLine(c.card) }}
            </NuxtLink>
            <span class="text-sm text-muted">{{ c.size }} mentions</span>
          </div>
          <p class="mt-1 text-sm text-muted">
            {{ c.languages.join(', ') || 'no language' }}
            <template v-if="c.event_types.length">
              · {{ c.event_types.join(', ') }}
            </template>
            <template v-if="c.max_significance !== null">
              · significance {{ c.max_significance }}
            </template>
          </p>
        </UCard>
      </li>
    </ul>
    <UPagination v-if="total > PER_PAGE" :page="params.page" :items-per-page="PER_PAGE" :total="total" @update:page="setQuery({ page: $event }, false)" />
  </div>
</template>
