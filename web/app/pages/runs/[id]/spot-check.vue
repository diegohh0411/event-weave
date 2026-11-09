<script setup lang="ts">
import { useApi } from '../../../api/client'
import type { components } from '../../../api/schema'

type Item = components['schemas']['SampleItem']
interface Row extends Item { range: [number, number] | null }

const api = useApi()
const route = useRoute()
const router = useRouter()
const id = String(route.params.id)

const one = (v: unknown) => (Array.isArray(v) ? v[0] : v) as string | undefined
const int = (v: unknown, fallback: number, min: number) => {
  const n = Number(one(v))
  return Number.isInteger(n) && n >= min ? n : fallback
}
const seed = computed(() => int(route.query.seed, 0, 0))
const n = computed(() => Math.min(200, int(route.query.n, 10, 1)))
const enricher = computed(() => one(route.query.enricher) ?? '')

function setQuery(patch: Record<string, string | number | undefined>) {
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries({ ...route.query, ...patch })) {
    const s = one(v)
    if (s !== undefined && s !== '') next[k] = String(s)
  }
  return router.replace({ query: next })
}
const reshuffle = () => setQuery({ seed: Math.floor(Math.random() * 1_000_000) })

const errorText = (r: { error?: unknown }) => {
  const detail = (r.error as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string' ? detail : 'the API rejected the request'
}

const rows = shallowRef<Row[]>([])
const loading = ref(true)
const error = ref<string>()
const enrichers = ref<string[]>([])
let seq = 0

const addEnrichers = (names: (string | undefined)[]) => {
  const all = new Set(enrichers.value)
  for (const x of names) if (x) all.add(x)
  enrichers.value = [...all]
}

const docs = new Map<string, Promise<components['schemas']['DocumentOut'] | undefined>>()
const documentOf = (did: string) => {
  let p = docs.get(did)
  if (!p) {
    p = api.GET('/runs/{run_id}/documents/{document_id}', { params: { path: { run_id: id, document_id: did } } })
      .then(r => r.data, () => undefined)
    docs.set(did, p)
  }
  return p
}

/** The trigger's code-point range inside its sentence: the document offset minus the sentence's own offset. */
async function triggerRange(item: Item): Promise<[number, number] | null> {
  const d = await documentOf(item.document_id)
  const m = d?.mentions.find(x => x.id === item.mention_id)
  if (!d || !m) return null
  const at = d.text.indexOf(item.sentence)
  if (at < 0) return null
  const offset = Array.from(d.text.slice(0, at)).length
  const start = m.start - offset
  const end = m.end - offset
  return start >= 0 && end <= Array.from(item.sentence).length ? [start, end] : null
}

async function load() {
  const mine = ++seq
  loading.value = true
  try {
    const r = await api.GET('/runs/{run_id}/enrichments/sample', {
      params: { path: { run_id: id }, query: { seed: seed.value, limit: n.value, ...(enricher.value ? { enricher: enricher.value } : {}) } }
    })
    if (mine !== seq) return
    if (!r.data) throw new Error(errorText(r))
    const items = r.data.items as Item[]
    const withRanges = await Promise.all(items.map(async i => ({ ...i, range: await triggerRange(i) })))
    if (mine !== seq) return
    rows.value = withRanges
    addEnrichers(items.map(i => i.enricher))
    error.value = undefined
  } catch (e) {
    if (mine !== seq) return
    rows.value = []
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}
watch([seed, n, enricher], load)

onMounted(async () => {
  load()
  try {
    const r = await api.GET('/runs/{run_id}', { params: { path: { run_id: id } } })
    addEnrichers([r.data?.config.stages.enrich?.adapter])
  } catch { /* the sample's own enrichers still fill the select */ }
})
onBeforeUnmount(() => { seq++ })

const list = (v: unknown) => (Array.isArray(v) ? v.join(', ') : '')
const show = (v: unknown) => (v === undefined || v === null || v === '' ? '—' : String(v))
const discarded = (r: Row) => Object.entries(r.discarded)
</script>

<template>
  <div class="space-y-6">
    <NuxtLink :to="`/runs/${id}`" class="text-sm text-primary hover:underline">
      Back to the run
    </NuxtLink>
    <h1 class="font-semibold">
      Enrichment spot check
    </h1>

    <div class="flex flex-wrap items-end gap-4">
      <label class="text-sm">
        <span class="block text-muted">Enricher</span>
        <select
          data-test="enricher"
          class="rounded border border-default bg-default px-2 py-1"
          :value="enricher"
          @change="setQuery({ enricher: ($event.target as HTMLSelectElement).value })"
        >
          <option value="">All</option>
          <option v-for="e in enrichers" :key="e" :value="e">{{ e }}</option>
        </select>
      </label>
      <label class="text-sm">
        <span class="block text-muted">Seed</span>
        <input
          data-test="seed"
          type="number"
          min="0"
          class="w-28 rounded border border-default bg-default px-2 py-1"
          :value="seed"
          @change="setQuery({ seed: int(($event.target as HTMLInputElement).value, 0, 0) })"
        >
      </label>
      <UButton data-test="reshuffle" color="neutral" variant="outline" @click="reshuffle">
        Reshuffle
      </UButton>
      <label class="text-sm">
        <span class="block text-muted">N</span>
        <input
          data-test="n"
          type="number"
          min="1"
          max="200"
          class="w-20 rounded border border-default bg-default px-2 py-1"
          :value="n"
          @change="setQuery({ n: Math.min(200, int(($event.target as HTMLInputElement).value, 10, 1)) })"
        >
      </label>
    </div>

    <UAlert v-if="error" color="error" variant="subtle" title="Could not load the sample" :description="error" />
    <p v-else-if="loading && !rows.length" class="text-muted">
      Loading sample...
    </p>
    <p v-else-if="!rows.length" class="text-muted">
      No enrichments in the sample.
    </p>
    <UCard v-for="r in rows" :key="r.id" data-test="sample">
      <div class="space-y-3">
        <p>
          <HighlightedText :text="r.sentence" :ranges="r.range ? [r.range] : []" />
        </p>
        <p class="text-xs text-muted">
          {{ r.enricher }} v{{ r.enricher_version }} ·
          <NuxtLink :to="`/runs/${id}/documents/${r.document_id}`" class="text-primary hover:underline">
            document
          </NuxtLink>
        </p>
        <dl class="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <dt class="text-sm text-muted">
              Event type
            </dt>
            <dd>{{ show(r.enrichment.event_type) }}</dd>
          </div>
          <div>
            <dt class="text-sm text-muted">
              Significance
            </dt>
            <dd>{{ show(r.enrichment.significance) }}</dd>
          </div>
          <div>
            <dt class="text-sm text-muted">
              Participants
            </dt>
            <dd class="break-words">
              {{ list(r.enrichment.participants) || '—' }}
            </dd>
          </div>
          <div>
            <dt class="text-sm text-muted">
              Location
            </dt>
            <dd>{{ show(r.enrichment.location) }}</dd>
          </div>
        </dl>
        <div v-if="discarded(r).length" class="text-sm">
          <p class="text-muted">
            Discarded ({{ discarded(r).length }})
          </p>
          <ul>
            <li v-for="[field, why] in discarded(r)" :key="field">
              <span class="font-medium">{{ field }}</span>: {{ why }}
            </li>
          </ul>
        </div>
      </div>
    </UCard>
  </div>
</template>
