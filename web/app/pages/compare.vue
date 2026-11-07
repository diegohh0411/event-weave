<script setup lang="ts">
import { useApi } from '../api/client'
import { fetchAll } from '../api/fetchAll'
import type { components } from '../api/schema'

type RunSummary = components['schemas']['RunSummary']
type Compare = components['schemas']['CompareOut']

const api = useApi()
const route = useRoute()
const router = useRouter()

const selected = computed<string[]>(() => {
  const v = route.query.run_id
  return (Array.isArray(v) ? v : v ? [v] : []).filter((s): s is string => !!s)
})

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
  const next = on ? [...selected.value.filter(s => s !== id), id] : selected.value.filter(s => s !== id)
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
    for (const language of [...langs].sort()) {
      out.push({ track, language, runs: rs, paper: track === '1' && d.paper ? d.paper.filter(p => p.language === language) : undefined })
    }
  }
  return out
})
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-xl font-semibold">
      Compare
    </h1>

    <fieldset class="space-y-1">
      <legend class="text-sm font-medium">
        Runs
      </legend>
      <UAlert v-if="runsError" color="error" variant="subtle" title="Could not load runs" :description="runsError" />
      <ul class="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
        <li v-for="r in runs" :key="r.id">
          <label class="flex min-w-0 items-center gap-2 text-sm">
            <input type="checkbox" :value="r.id" :checked="selected.includes(r.id)" @change="toggle(r.id, ($event.target as HTMLInputElement).checked)">
            <span class="min-w-0 break-words">{{ r.name }} <span class="text-muted">({{ r.track }}, {{ r.state }})</span></span>
          </label>
        </li>
      </ul>
    </fieldset>

    <p v-if="!selected.length" class="text-muted">
      Select at least one run to compare.
    </p>
    <UAlert v-else-if="error" color="error" variant="subtle" title="Could not load the comparison" :description="error" />
    <p v-else-if="loading && !data" class="text-muted">
      Loading comparison...
    </p>
    <template v-else-if="data">
      <UAlert v-if="data.paper && !data.paper_checked" color="warning" variant="subtle" title="Paper numbers UNCHECKED" description="The MCECR paper numbers have not been checked against the PDF." />

      <section v-if="unevaluated.length" class="space-y-2">
        <h2 class="font-medium">
          Not evaluated
        </h2>
        <div class="overflow-x-auto rounded-md border border-default">
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="r in unevaluated" :key="r.run_id" class="border-t border-default first:border-t-0">
                <th scope="row" class="px-3 py-2 text-left font-medium">
                  {{ r.name }}
                </th>
                <td class="px-3 py-2 text-muted">
                  Not evaluated (run weave eval {{ r.run_id }})
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-for="g in groups" :key="g.track + g.language" class="space-y-2">
        <h2 class="font-medium">
          Track {{ g.track }} · {{ g.language }}
        </h2>
        <ScoreTable :track="g.track" :language="g.language" :runs="g.runs" :paper="g.paper" />
      </section>
    </template>
  </div>
</template>
