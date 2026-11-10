<script setup lang="ts">
import { useApi } from '../api/client'
import { fetchAll } from '../api/fetchAll'
import type { components } from '../api/schema'

type RunSummary = components['schemas']['RunSummary']
type Compare = components['schemas']['CompareOut']

const api = useApi()
const route = useRoute()
const router = useRouter()

// The API rejects comparisons of more than 8 runs.
const MAX_RUNS = 8
const requested = computed<string[]>(() => {
  const v = route.query.run_id
  return [...new Set((Array.isArray(v) ? v : v ? [v] : []).filter((s): s is string => !!s))]
})
const selected = computed(() => requested.value.slice(0, MAX_RUNS))
const atLimit = computed(() => selected.value.length >= MAX_RUNS)

const runs = ref<RunSummary[]>([])
const runsError = ref<string>()
onMounted(async () => {
  try {
    runs.value = await fetchAll(async (offset, limit) => (await api.GET('/runs', { params: { query: { offset, limit } } })).data)
  } catch (e) {
    runsError.value = e instanceof Error ? e.message : String(e)
  }
})

function toggle(id: string, on: boolean) {
  const next = on ? [...selected.value.filter(s => s !== id), id].slice(0, MAX_RUNS) : selected.value.filter(s => s !== id)
  return router.replace({ query: { ...route.query, run_id: next.length ? next : undefined } })
}

const data = ref<Compare>()
const loading = ref(false)
const error = ref<string>()
let seq = 0

async function load() {
  const mine = ++seq
  const ids = selected.value
  if (!ids.length) {
    data.value = undefined
    error.value = undefined
    loading.value = false
    return
  }
  loading.value = true
  try {
    const r = await api.GET('/compare', { params: { query: { run_id: ids } } })
    if (mine !== seq) return // a newer selection replaced this request
    if (!r.data) {
      const detail = (r.error as { detail?: unknown } | undefined)?.detail
      throw new Error(typeof detail === 'string' ? detail : 'the API rejected the request')
    }
    data.value = r.data as Compare
    error.value = undefined
  } catch (e) {
    if (mine !== seq) return
    data.value = undefined
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}
watch(selected, load, { immediate: true })

const isTrack1 = (t: string) => t === 'paper'
const unevaluated = computed(() => (data.value?.runs ?? []).filter(r => !r.evaluated))

// One table per (track, language). Track 1 gets its Hindi cross reason from `skipped`
// even when no score exists for that language/setting.
const groups = computed(() => {
  const d = data.value
  if (!d) return []
  const out: { track: '1' | '2', language: string, runs: typeof d.runs, paper?: NonNullable<Compare['paper']> }[] = []
  for (const track of ['1', '2'] as const) {
    const rs = d.runs.filter(r => r.evaluated && isTrack1(r.track) === (track === '1'))
    const langs = new Set<string>()
    for (const r of rs) {
      r.scores.forEach(s => langs.add(s.language))
      if (track === '1') Object.keys(r.skipped).forEach(k => langs.add(k.split('/')[0]!))
    }
    // Paper rows stand on their own: they show even when no Track 1 run has been evaluated.
    if (track === '1') d.paper?.forEach(p => langs.add(p.language))
    for (const language of [...langs].sort()) {
      out.push({ track, language, runs: rs, paper: track === '1' && d.paper ? d.paper.filter(p => p.language === language) : undefined })
    }
  }
  return out
})
</script>

<template>
  <div>
    <PageHeader title="Compare" description="Coreference scores, cost and speed for the runs you select, one table per track and language." />

    <fieldset class="mb-8 space-y-3 rounded-xl border border-default bg-default p-4">
      <legend class="sr-only">
        Runs
      </legend>
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <p class="text-sm font-bold text-highlighted">
          Runs <span class="tabular font-normal text-muted">{{ selected.length }} of {{ MAX_RUNS }} selected</span>
        </p>
        <p class="text-xs text-muted">
          Compare at most {{ MAX_RUNS }} runs{{ atLimit ? '; deselect one to pick another' : '' }}.
        </p>
      </div>
      <UAlert v-if="runsError" color="error" variant="subtle" title="Could not load runs" :description="runsError" />
      <UAlert v-if="requested.length > MAX_RUNS" color="warning" variant="subtle" title="Too many runs" :description="`Only the first ${MAX_RUNS} of ${requested.length} runs are compared.`" />
      <ul class="flex flex-wrap gap-2">
        <li v-for="r in runs" :key="r.id">
          <label
            class="flex min-w-0 cursor-pointer items-center gap-2 rounded-full border py-1.5 pl-2.5 pr-3.5 text-sm transition-colors has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-[var(--ew-thread)]"
            :class="selected.includes(r.id) ? 'border-primary bg-primary/10 text-highlighted' : 'border-default hover:border-accented'"
          >
            <input type="checkbox" class="size-4 accent-[var(--ui-primary)]" :value="r.id" :checked="selected.includes(r.id)" :disabled="atLimit && !selected.includes(r.id)" @change="toggle(r.id, ($event.target as HTMLInputElement).checked)">
            <span class="min-w-0 break-words font-medium">{{ r.name }} <span class="font-normal text-muted">({{ r.track }}, {{ r.state }})</span></span>
          </label>
        </li>
      </ul>
    </fieldset>

    <p v-if="!selected.length" class="rounded-xl border border-dashed border-accented p-5 text-sm text-muted">
      Select at least one run to compare.
    </p>
    <UAlert v-else-if="error" color="error" variant="subtle" title="Could not load the comparison" :description="error" />
    <p v-else-if="loading && !data" class="text-muted">
      Loading comparison...
    </p>
    <div v-else-if="data" class="space-y-8">
      <UAlert v-if="data.paper && !data.paper_checked" color="warning" variant="subtle" title="Paper numbers UNCHECKED" description="The MCECR paper numbers have not been checked against the PDF." />

      <section v-if="unevaluated.length" class="space-y-3">
        <h2 class="text-base font-bold text-highlighted">
          Not evaluated
        </h2>
        <div class="overflow-x-auto rounded-xl border border-default bg-default">
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="r in unevaluated" :key="r.run_id" class="border-t border-default first:border-t-0">
                <th scope="row" class="px-4 py-3 text-left font-semibold text-highlighted">
                  {{ r.name }}
                </th>
                <td class="px-4 py-3 text-muted">
                  Not evaluated (run weave eval {{ r.run_id }})
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-for="g in groups" :key="g.track + g.language" class="space-y-3">
        <h2 class="flex items-center gap-2 text-base font-bold text-highlighted">
          Track {{ g.track }} <UBadge color="neutral" variant="outline" size="sm">{{ g.language }}</UBadge>
        </h2>
        <ScoreTable :track="g.track" :language="g.language" :runs="g.runs" :paper="g.paper" />
      </section>
    </div>
  </div>
</template>
