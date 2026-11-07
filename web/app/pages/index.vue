<script setup lang="ts">
import { useApi } from '../api/client'
import { fetchAll } from '../api/fetchAll'
import type { components } from '../api/schema'
import { buildRunConfig, STAGES, type BuilderState, type Capability, type DeploymentOut, type Stage } from '../utils/runConfig'
import { changedSettings, fieldsFromSchema } from '../utils/schemaForm'

type AdapterOut = components['schemas']['AdapterOut']
type EstimateOut = components['schemas']['EstimateOut']

const api = useApi()
const NONE = '__none__'
const STAGE_LABEL: Record<Stage, string> = {
  provide: 'Provider', extract: 'Extract', tag: 'Tag', enrich: 'Enrich', embed: 'Embed', cluster: 'Cluster'
}

const adapters = ref<AdapterOut[]>([])
const deployments = ref<DeploymentOut[]>([])
const loading = ref(true)
const loadError = ref<string>()

const name = ref('my-run')
const track = ref<'paper' | 'realistic'>('paper')
const seed = ref(0)
const chosen = reactive<Record<Stage, string | undefined>>({ provide: undefined, extract: undefined, tag: undefined, enrich: undefined, embed: undefined, cluster: undefined })
const settings = reactive<Record<Stage, Record<string, unknown>>>({ provide: {}, extract: {}, tag: {}, enrich: {}, embed: {}, cluster: {} })
const picked = reactive<Record<Stage, Record<string, string>>>({ provide: {}, extract: {}, tag: {}, enrich: {}, embed: {}, cluster: {} })
const jsonInvalid = reactive<Record<Stage, boolean>>({ provide: false, extract: false, tag: false, enrich: false, embed: false, cluster: false })

const adapterFor = (stage: Stage) => adapters.value.find(a => a.stage === stage && a.name === chosen[stage])
const optionsFor = (stage: Stage) => adapters.value.filter(a => a.stage === stage)
const fieldsFor = (stage: Stage) => fieldsFromSchema(adapterFor(stage)?.settings_schema ?? {})

function setAdapter(stage: Stage, value: string | undefined) {
  const next = value === NONE ? undefined : value
  if (next === chosen[stage]) return
  chosen[stage] = next
  settings[stage] = {}
  picked[stage] = {}
  jsonInvalid[stage] = false
  reset()
}

async function load() {
  try {
    const [a, d] = await Promise.all([
      fetchAll((offset, limit) => api.GET('/adapters', { params: { query: { offset, limit } } }).then(r => r.data)),
      fetchAll((offset, limit) => api.GET('/deployments', { params: { query: { offset, limit } } }).then(r => r.data))
    ])
    adapters.value = a
    deployments.value = d
    for (const stage of STAGES) {
      if (stage !== 'tag' && !chosen[stage]) chosen[stage] = optionsFor(stage)[0]?.name
    }
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const capabilities = (stage: Stage): Capability[] => adapterFor(stage)?.capabilities ?? []
const missing = computed(() => STAGES.flatMap(stage => capabilities(stage)
  .filter(c => !picked[stage][c] || !deployments.value.some(d => d.ready && d.capability === c && d.id === picked[stage][c]))
  .map(c => `${stage}: no ready deployment for ${c}`)))

const state = computed<BuilderState>(() => {
  const stages = {} as BuilderState['stages']
  for (const stage of STAGES) {
    const a = adapterFor(stage)
    if (!a) { stages[stage] = undefined; continue }
    const fields = fieldsFor(stage)
    stages[stage] = {
      adapter: a.name,
      settings: changedSettings(fields, settings[stage]),
      deployments: Object.fromEntries(a.capabilities.flatMap(c => (picked[stage][c] ? [[c, picked[stage][c]!]] : [])))
    }
  }
  return { name: name.value, track: track.value, seed: seed.value, stages }
})
const config = computed(() => buildRunConfig(state.value))

const problems = ref<string[]>([])
const estimate = ref<EstimateOut | null>(null)
const busy = ref(false)
const validated = ref(false)

// Bumped on every config change; a response for an older config is dropped.
let generation = 0
function reset() {
  generation++
  problems.value = []
  validated.value = false
  estimate.value = null
  busy.value = false
}
watch(config, reset, { deep: true })

/** ConfigError gives {problems}; request validation gives FastAPI's {detail: [{loc, msg}]}. */
function problemsOf(error: unknown): string[] {
  const e = error as { problems?: string[], detail?: unknown } | undefined
  if (e?.problems?.length) return e.problems
  if (Array.isArray(e?.detail)) return e.detail.map((d: { loc?: unknown[], msg?: string }) => `${(d.loc ?? []).join('.')}: ${d.msg}`)
  if (typeof e?.detail === 'string') return [e.detail]
  return ['The API rejected the request.']
}

async function validate() {
  busy.value = true
  problems.value = []
  const mine = generation
  try {
    const [v, est] = await Promise.all([
      api.POST('/runs/validate', { body: config.value }),
      api.POST('/runs/estimate', { body: config.value })
    ])
    if (mine !== generation) return
    estimate.value = est.data ?? null
    if (v.error) problems.value = problemsOf(v.error)
    else if (est.error) problems.value = problemsOf(est.error)
    validated.value = problems.value.length === 0
  } catch (e) {
    if (mine === generation) problems.value = [e instanceof Error ? e.message : String(e)]
  } finally {
    if (mine === generation) busy.value = false
  }
}

async function launch() {
  busy.value = true
  problems.value = []
  try {
    const created = await api.POST('/runs', { body: config.value })
    if (!created.data) { problems.value = problemsOf(created.error); return }
    const started = await api.POST('/runs/{run_id}/start', { params: { path: { run_id: created.data.run_id } } })
    if (started.error) { problems.value = problemsOf(started.error); return }
    await navigateTo(`/runs/${created.data.run_id}`)
  } catch (e) {
    problems.value = [e instanceof Error ? e.message : String(e)]
  } finally {
    busy.value = false
  }
}

const blocked = computed(() => busy.value || problems.value.length > 0 || missing.value.length > 0
  || Object.values(jsonInvalid).some(Boolean) || !name.value.trim())
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-xl font-semibold">
      Builder
    </h1>

    <p v-if="loading" class="text-muted">
      Loading adapters and deployments...
    </p>
    <UAlert v-else-if="loadError" color="error" variant="subtle" title="Could not reach the API" :description="loadError" />

    <form v-else class="space-y-6" @submit.prevent="validate">
      <UCard>
        <template #header>
          <h2 class="font-semibold">
            Run
          </h2>
        </template>
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <UFormField label="Name">
            <UInput v-model="name" class="w-full" />
          </UFormField>
          <UFormField label="Track">
            <USelect v-model="track" class="w-full" :items="[{ label: 'paper (Track 1)', value: 'paper' }, { label: 'realistic', value: 'realistic' }]" />
          </UFormField>
          <UFormField label="Seed">
            <UInput v-model.number="seed" class="w-full" type="number" step="1" />
          </UFormField>
        </div>
      </UCard>

      <UCard v-for="stage in STAGES" :key="stage">
        <template #header>
          <h2 class="font-semibold">
            {{ STAGE_LABEL[stage] }}
          </h2>
        </template>
        <div class="space-y-4">
          <UFormField :label="stage === 'provide' ? 'Provider' : 'Adapter'">
            <USelect
              class="w-full"
              :items="[...(stage === 'tag' ? [{ label: 'None', value: NONE }] : []), ...optionsFor(stage).map(a => ({ label: a.name, value: a.name }))]"
              :model-value="chosen[stage] ?? (stage === 'tag' ? NONE : undefined)"
              @update:model-value="(v: string) => setAdapter(stage, v)"
            />
          </UFormField>
          <SchemaForm
            v-if="chosen[stage]"
            :key="chosen[stage]"
            v-model="settings[stage]"
            :fields="fieldsFor(stage)"
            @update:invalid="(v: boolean) => (jsonInvalid[stage] = v)"
          />
          <div v-if="capabilities(stage).length" class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <DeploymentPicker
              v-for="cap in capabilities(stage)"
              :key="`${chosen[stage]}-${cap}`"
              :capability="cap"
              :deployments="deployments"
              :model-value="picked[stage][cap]"
              @update:model-value="(id: string | undefined) => { if (id) picked[stage][cap] = id; else delete picked[stage][cap] }"
            />
          </div>
        </div>
      </UCard>

      <EstimatePanel :estimate="estimate" />

      <UAlert
        v-if="problems.length"
        color="error"
        variant="subtle"
        title="This run cannot be launched yet"
      >
        <template #description>
          <ul class="list-disc pl-5">
            <li v-for="p in problems" :key="p">
              {{ p }}
            </li>
          </ul>
        </template>
      </UAlert>
      <UAlert
        v-else-if="validated"
        color="success"
        variant="subtle"
        title="Configuration is valid"
      />
      <UAlert
        v-if="missing.length"
        color="warning"
        variant="subtle"
        title="Launch is disabled"
      >
        <template #description>
          <ul class="list-disc pl-5">
            <li v-for="m in missing" :key="m">
              {{ m }}
            </li>
          </ul>
        </template>
      </UAlert>

      <div class="flex flex-wrap gap-3">
        <UButton type="button" variant="outline" :loading="busy" :disabled="busy || Object.values(jsonInvalid).some(Boolean)" @click="validate">
          Validate
        </UButton>
        <UButton type="button" :disabled="blocked" @click="launch">
          Launch
        </UButton>
      </div>
    </form>
  </div>
</template>
