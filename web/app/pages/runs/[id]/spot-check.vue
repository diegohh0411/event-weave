<script setup lang="ts">
import { useApi } from '../../../api/client'
import type { components } from '../../../api/schema'

type Item = components['schemas']['SampleItem']
interface Row extends Item { range: [number, number] | null }
interface Field { text?: unknown }

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

const OPEN = '⟦'
const CLOSE = '⟧'

/** Splits a sentence carrying its trigger as ⟦...⟧ into the clean sentence and the trigger's code-point range. */
function parseMarked(marked: string): { sentence: string, range: [number, number] | null } {
  const open = marked.indexOf(OPEN)
  const close = marked.indexOf(CLOSE, open + 1)
  if (open < 0 || close < 0) return { sentence: marked.replaceAll(OPEN, '').replaceAll(CLOSE, ''), range: null }
  const before = marked.slice(0, open)
  const inside = marked.slice(open + 1, close)
  const start = Array.from(before).length
  return { sentence: before + inside + marked.slice(close + 1), range: [start, start + Array.from(inside).length] }
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
    rows.value = items.map((i) => {
      const { sentence, range } = parseMarked(i.sentence)
      return { ...i, sentence, range }
    })
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

// Each of the four fields is `{text, ...}` or null (not found).
const FIELDS = [['action', 'Action'], ['participants', 'Participants'], ['location', 'Location'], ['time', 'Time']] as const
const fieldText = (r: Row, name: string) => {
  const t = (r.enrichment[name] as Field | null | undefined)?.text
  return typeof t === 'string' && t ? t : '—'
}
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
          <div v-for="[name, label] in FIELDS" :key="name" :data-test="`field-${name}`">
            <dt class="text-sm text-muted">
              {{ label }}
            </dt>
            <dd class="break-words">
              {{ fieldText(r, name) }}
            </dd>
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
