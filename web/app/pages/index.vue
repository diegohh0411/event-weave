<script setup lang="ts">
import { useApi } from '../api/client'
import { fetchAll } from '../api/fetchAll'
import type { components } from '../api/schema'
import { buildRunConfig, STAGES, type BuilderState, type Capability, type DeploymentOut, type Stage } from '../utils/runConfig'
import { changedSettings, fieldsFromSchema } from '../utils/schemaForm'
import { apiError } from '../utils/apiError'

type AdapterOut = components['schemas']['AdapterOut']
type EstimateOut = components['schemas']['EstimateOut']

const api = useApi()
const NONE = '__none__'
const STAGE_LABEL: Record<Stage, string> = {
  provide: 'Provider', extract: 'Extract', tag: 'Tag', enrich: 'Enrich', embed: 'Embed', cluster: 'Cluster'
}
const STAGE_HINT: Record<Stage, string> = {
  provide: 'Where the documents come from.',
  extract: 'Finds event mentions in each document.',
  tag: 'Optional. Labels each mention with an event type.',
  enrich: 'Describes each mention by action, participants, location and time.',
  embed: 'Turns mentions into vectors for retrieval.',
  cluster: 'Groups mentions of the same real-world event.'
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
const fieldsFor = (stage: Stage) => fieldsFromSchema(adapterFor(stage)?.settings_schema ?? {}, stage === 'provide' ? ['crawl_id'] : [])

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

const probing = ref(false)
const probeError = ref('')
async function reprobe() {
  if (probing.value) return
  probing.value = true
  probeError.value = ''
  reset()
  try {
    const result = await api.POST('/deployments/probe')
    if (!result.data) throw new Error(apiError(result.error))
    deployments.value = await fetchAll(async (offset, limit) => {
      const page = await api.GET('/deployments', { params: { query: { offset, limit } } })
      if (!page.data) throw new Error(apiError(page.error))
      return page.data
    })
  } catch (e) { probeError.value = e instanceof Error ? e.message : String(e) }
  finally { probing.value = false }
}

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
const matchCount = ref<number | null>(null)
const matchError = ref('')
let facetGeneration = 0
watch(() => [chosen.provide, settings.provide] as const, async () => {
  const mine = ++facetGeneration
  if (chosen.provide !== 'replay') { matchCount.value = null; return }
  const { provider, outlets, languages, published_from, published_to, search, limit } = settings.provide
  try {
    const result = await api.GET('/library/facets', { params: { query: {
      providers: [typeof provider === 'string' ? provider : 'crawl'],
      outlets: Array.isArray(outlets) ? outlets as string[] : undefined,
      languages: Array.isArray(languages) ? languages as string[] : undefined,
      published_from: typeof published_from === 'string' ? published_from : undefined,
      published_to: typeof published_to === 'string' ? published_to : undefined,
      search: typeof search === 'string' ? search : undefined
    } } })
    if (!result.data) throw new Error(apiError(result.error))
    if (mine === facetGeneration) { matchCount.value = typeof limit === 'number' ? Math.min(result.data.total, limit) : result.data.total; matchError.value = '' }
  } catch (e) { if (mine === facetGeneration) { matchCount.value = null; matchError.value = e instanceof Error ? e.message : String(e) } }
}, { deep: true, immediate: true })

const problems = ref<string[]>([])
const estimate = ref<EstimateOut | null>(null)
const busy = ref(false)
// Set only by launch() itself, so a config change cannot re-enable a second Launch.
const launching = ref(false)
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
  if (probing.value || busy.value || launching.value) return
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
  if (launching.value) return
  launching.value = true
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
    launching.value = false
    busy.value = false
  }
}

const blocked = computed(() => launching.value || busy.value || probing.value || problems.value.length > 0 || missing.value.length > 0
  || Object.values(jsonInvalid).some(Boolean) || !name.value.trim())
</script>

<template>
  <div>
    <PageHeader title="New run" description="Pick an implementation for each stage of the pipeline, validate to see the estimate, then launch.">
      <template #actions>
        <UButton type="button" color="neutral" variant="outline" :loading="probing" :disabled="loading || probing || launching || busy" @click="reprobe">
          Re-probe deployments
        </UButton>
      </template>
    </PageHeader>

    <UAlert v-if="probeError" class="mb-6" color="error" variant="subtle" title="Could not re-probe deployments" :description="probeError" />

    <p v-if="loading" class="text-muted">
      Loading adapters and deployments...
    </p>
    <UAlert v-else-if="loadError" color="error" variant="subtle" title="Could not reach the API" :description="loadError" />

    <form v-else @submit.prevent="validate">
      <fieldset :disabled="launching" class="min-w-0 border-0 p-0">
        <div class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div class="min-w-0 space-y-8">
            <section class="rounded-xl border border-default bg-default p-5">
              <h2 class="mb-4 text-base font-bold text-highlighted">
                Run
              </h2>
              <div class="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,2fr)_minmax(0,1.3fr)_minmax(0,1fr)]">
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
            </section>

            <section>
              <h2 class="mb-4 text-base font-bold text-highlighted">
                Pipeline
              </h2>
              <!-- The stages run in this order; the thread on the left ties them together. -->
              <ol class="relative space-y-4">
                <span aria-hidden="true" class="absolute bottom-6 left-[0.9375rem] top-6 w-px bg-[var(--ew-thread)] opacity-30" />
                <li v-for="(stage, n) in STAGES" :key="stage" class="relative pl-11">
                  <span
                    aria-hidden="true"
                    class="tabular absolute left-0 top-4 grid size-8 place-items-center rounded-full border-2 text-xs font-bold"
                    :class="chosen[stage] ? 'border-[var(--ew-thread)] bg-[var(--ew-thread)] text-white' : 'border-[var(--ew-thread)]/40 bg-[var(--ew-loom)] text-muted'"
                  >{{ n + 1 }}</span>
                  <div class="rounded-xl border border-default bg-default p-5">
                    <div class="mb-4">
                      <h3 class="font-bold text-highlighted">
                        {{ STAGE_LABEL[stage] }}
                      </h3>
                      <p class="text-sm text-muted">
                        {{ STAGE_HINT[stage] }}
                      </p>
                    </div>
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
                      <p v-if="stage === 'provide' && chosen.provide === 'replay' && matchCount !== null" class="text-sm text-muted">{{ matchCount }} documents match</p>
                      <p v-if="stage === 'provide' && matchError" class="text-sm text-error">Could not load library count: {{ matchError }}</p>
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
                  </div>
                </li>
              </ol>
            </section>
          </div>

          <aside class="min-w-0">
            <div class="space-y-4 lg:sticky lg:top-8">
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

              <div class="grid grid-cols-2 gap-2">
                <UButton type="button" color="neutral" variant="outline" size="lg" block :loading="busy" :disabled="launching || busy || probing || Object.values(jsonInvalid).some(Boolean)" @click="validate">
                  Validate
                </UButton>
                <UButton type="button" size="lg" block :disabled="blocked" @click="launch">
                  Launch
                </UButton>
              </div>
            </div>
          </aside>
        </div>
      </fieldset>
    </form>
  </div>
</template>
