<script setup lang="ts">
import type { components } from '../api/schema'

type Run = components['schemas']['CompareRunOut']
type PaperRow = components['schemas']['CompareRowOut']

const props = defineProps<{
  track: '1' | '2'
  language: string
  runs: Run[]
  paper?: PaperRow[]
}>()

const METRICS = [
  { key: 'muc', label: 'MUC' }, { key: 'b3', label: 'B³' }, { key: 'ceafe', label: 'CEAF-e' },
  { key: 'blanc', label: 'BLANC' }, { key: 'conll', label: 'CoNLL F1' }
] as const

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '–' : v.toFixed(1))
const one = (v: number | null | undefined) => (v === null || v === undefined ? '–' : v.toFixed(1))
const dollars = (v: string | null | undefined) => (v === null || v === undefined ? '–' : `$${Number(v).toFixed(2)}`)
// The scorer writes trigger scores keyed by language.
const trig = (r: Run) => (r.trigger?.[props.language] ?? null) as { precision?: number, recall?: number, f1?: number } | null
const failureCount = (r: Run) => Object.values(r.failures).reduce((a, byKey) => a + Object.values(byKey).reduce((x, y) => x + y, 0), 0)
const failureTitle = (r: Run) => Object.entries(r.failures).map(([stage, byKey]) => `${stage}: ${Object.values(byKey).reduce((x, y) => x + y, 0)}`).join(', ')

const ORDER = ['within', 'cross', 'pooled']
const settings = computed(() => {
  const set = new Set<string>()
  for (const r of props.runs) {
    for (const s of r.scores) if (s.language === props.language) set.add(s.setting)
    for (const k of Object.keys(r.skipped)) {
      const [l, s] = k.split('/')
      if (l === props.language && s) set.add(s)
    }
  }
  for (const p of props.paper ?? []) set.add(p.setting)
  return [...set].sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b))
})

const cell = (r: Run, setting: string, metric: string) => {
  const s = r.scores.find(x => x.language === props.language && x.setting === setting && x.metric === metric)
  if (!s) return '–'
  return s.f1_low !== null && s.f1_high !== null ? `${pct(s.f1)} [${pct(s.f1_low)}–${pct(s.f1_high)}]` : pct(s.f1)
}

const blocks = computed(() => settings.value.map((setting) => {
  const runRows = props.runs.flatMap((run) => {
    if (!run.evaluated) return []
    const skipped = run.skipped[`${props.language}/${setting}`]
    const has = run.scores.some(s => s.language === props.language && s.setting === setting)
    if (!has && !skipped) return []
    return [{ run, setting, skipped: has ? undefined : skipped }]
  })
  const rows = (props.paper ?? []).filter(p => p.setting === setting)
  const keys = [...new Set(rows.flatMap(p => Object.keys(p.paper).map(model => `${p.regime}\u0000${model}`)))]
  const paperRows = keys.map((key) => {
    const [regime, model] = key.split('\u0000') as [string, string]
    const mine = rows.filter(p => p.regime === regime && model in p.paper)
    const f1 = (metric: string) => {
      const v = mine.find(p => p.metric === metric)?.paper[model]
      return typeof v === 'number' ? v : v?.f1
    }
    return { model, setting, regime, label: mine[0]!.label, f1 }
  })
  return { setting, runRows, paperRows }
}))
</script>

<template>
  <div class="overflow-x-auto rounded-xl border border-default bg-default">
    <table data-test="score-table" :data-track="track" :data-language="language" class="tabular w-full min-w-max text-sm">
      <thead class="border-b border-default text-left text-muted">
        <tr>
          <th class="px-4 py-2.5 font-medium">
            Run / setting
          </th>
          <th v-for="m in METRICS" :key="m.key" class="px-4 py-2.5 font-medium">
            {{ m.label }}
          </th>
          <template v-if="track === '2'">
            <th class="px-4 py-2.5 font-medium">
              Trigger P
            </th>
            <th class="px-4 py-2.5 font-medium">
              Trigger R
            </th>
            <th class="px-4 py-2.5 font-medium">
              Trigger F1
            </th>
          </template>
          <th class="px-4 py-2.5 font-medium">
            $/1k mentions
          </th>
          <th class="px-4 py-2.5 font-medium">
            Mentions/hour
          </th>
          <th class="px-4 py-2.5 font-medium">
            Failures
          </th>
        </tr>
      </thead>
      <tbody>
        <template v-for="b in blocks" :key="b.setting">
          <tr v-for="r in b.runRows" :key="r.run.run_id + b.setting" data-test="run-row" class="border-t border-default">
            <th scope="row" class="px-4 py-3 text-left font-semibold text-highlighted">
              {{ r.run.name }} <span class="font-normal text-muted">({{ b.setting }})</span>
            </th>
            <td v-if="r.skipped" :colspan="METRICS.length" data-test="skipped" class="px-4 py-3 text-muted">
              {{ r.skipped }}
            </td>
            <td v-for="m in METRICS" v-else :key="m.key" class="whitespace-nowrap px-4 py-3">
              {{ cell(r.run, b.setting, m.key) }}
            </td>
            <template v-if="track === '2'">
              <td class="px-4 py-3">
                {{ pct(trig(r.run)?.precision) }}
              </td>
              <td class="px-4 py-3">
                {{ pct(trig(r.run)?.recall) }}
              </td>
              <td class="px-4 py-3">
                {{ pct(trig(r.run)?.f1) }}
              </td>
            </template>
            <td class="px-4 py-3">
              {{ dollars(r.run.cost.dollars_per_1000_mentions) }}<span v-if="r.run.cost.any_estimated" class="text-muted"> est.</span>
            </td>
            <td class="px-4 py-3">
              {{ one(r.run.cost.mentions_per_hour) }}
            </td>
            <td class="px-4 py-3" :title="failureTitle(r.run)">
              {{ failureCount(r.run) }}
            </td>
          </tr>
          <tr v-for="p in b.paperRows" :key="b.setting + p.regime + p.model" data-test="paper-row" class="border-t border-dashed border-default bg-muted text-muted">
            <th scope="row" class="px-4 py-3 text-left font-normal">
              <div>MCECR paper ({{ p.model }}) <span>({{ b.setting }})</span></div>
              <div class="text-xs">
                {{ p.regime }}: {{ p.label }}
              </div>
            </th>
            <td v-for="m in METRICS" :key="m.key" class="px-4 py-3">
              {{ pct(p.f1(m.key.toString())) }}
            </td>
            <td :colspan="(track === '2' ? 3 : 0) + 3" class="px-4 py-3" />
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>
