<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'

type RunOut = components['schemas']['RunOut']
type Failure = components['schemas']['FailureOut']

const POLL_MS = 5000
const PER_PAGE = 20
const api = useApi()
const toast = useToast()
const id = String(useRoute().params.id)

const run = ref<RunOut>()
const loading = ref(true)
const loadError = ref<string>()

async function refresh() {
  const r = await api.GET('/runs/{run_id}', { params: { path: { run_id: id } } })
  if (r.data) run.value = r.data
  else if (!run.value) throw new Error(`run ${id} not found`)
}

let timer: ReturnType<typeof setInterval> | undefined
let polling = false
function stopPolling() {
  if (timer !== undefined) clearInterval(timer)
  timer = undefined
}
function startPolling() {
  if (timer !== undefined) return
  timer = setInterval(async () => {
    if (polling) return
    polling = true
    try { await refresh() } catch { /* keep the last good state; the next tick retries */ } finally { polling = false }
  }, POLL_MS)
}
watch(() => run.value?.state, (s) => { if (s === 'running') startPolling(); else stopPolling() })
onBeforeUnmount(stopPolling)

onMounted(async () => {
  try { await refresh() } catch (e) { loadError.value = e instanceof Error ? e.message : String(e) } finally { loading.value = false }
  loadFailures()
})

// Failures table, paged.
const failures = ref<Failure[]>([])
const failureTotal = ref(0)
const failurePage = ref(1)
async function loadFailures() {
  const r = await api.GET('/runs/{run_id}/failures', { params: { path: { run_id: id }, query: { offset: (failurePage.value - 1) * PER_PAGE, limit: PER_PAGE } } })
  if (r.data) { failures.value = r.data.items; failureTotal.value = r.data.total }
}
watch(failurePage, loadFailures)
watch(() => run.value?.state, loadFailures)
const failureColumns: TableColumn<Failure>[] = [
  { accessorKey: 'stage', header: 'Stage' },
  { accessorKey: 'unit_kind', header: 'Unit' },
  { accessorKey: 'unit_id', header: 'Id' },
  { accessorKey: 'attempts', header: 'Attempts' },
  { accessorKey: 'error', header: 'Error' }
]

// Start / Cancel / Resume. One request at a time: `acting` is set synchronously and cleared after.
const acting = ref(false)
const ACTIONS = { created: 'start', running: 'cancel', cancelled: 'resume' } as const
const LABEL = { start: 'Start', cancel: 'Cancel', resume: 'Resume' } as const
const action = computed(() => (run.value ? ACTIONS[run.value.state as keyof typeof ACTIONS] : undefined))

async function act() {
  const a = action.value
  if (!a || acting.value) return
  acting.value = true
  try {
    const r = await api.POST(`/runs/{run_id}/${a}` as '/runs/{run_id}/start', { params: { path: { run_id: id } } })
    if (r.data) run.value = r.data
    else {
      const detail = (r.error as { detail?: unknown } | undefined)?.detail
      toast.add({ title: `Could not ${a} the run`, description: typeof detail === 'string' ? detail : 'The API rejected the request.', color: 'error' })
    }
  } catch (e) {
    toast.add({ title: `Could not ${a} the run`, description: e instanceof Error ? e.message : String(e), color: 'error' })
  } finally {
    acting.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <p v-if="loading" class="text-muted">
      Loading run...
    </p>
    <UAlert v-else-if="loadError || !run" color="error" variant="subtle" title="Could not load the run" :description="loadError" />
    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <h1 class="break-words text-xl font-semibold">
            {{ run.name }}
          </h1>
          <p class="text-sm text-muted">
            {{ run.track }} · {{ run.state }}
          </p>
        </div>
        <UButton v-if="action" :color="action === 'cancel' ? 'error' : 'primary'" :loading="acting" :disabled="acting" @click="act">
          {{ LABEL[action] }}
        </UButton>
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <UCard>
          <p class="text-sm text-muted">
            Dollars so far
          </p>
          <p class="text-lg font-semibold">
            ${{ run.dollars }}<span v-if="run.dollars_estimated" class="ml-2 text-sm font-normal text-muted">estimated</span>
          </p>
        </UCard>
        <UCard>
          <p class="text-sm text-muted">
            Mentions per hour
          </p>
          <p class="text-lg font-semibold">
            {{ run.mentions_per_hour === null ? '—' : Math.round(run.mentions_per_hour) }}
          </p>
        </UCard>
      </div>

      <nav class="flex flex-wrap gap-4">
        <NuxtLink :to="`/runs/${id}/clusters`" class="text-primary hover:underline">
          Clusters
        </NuxtLink>
        <NuxtLink :to="`/runs/${id}/spot-check`" class="text-primary hover:underline">
          Spot check
        </NuxtLink>
        <NuxtLink :to="`/runs/${id}/scores`" class="text-primary hover:underline">
          Scores
        </NuxtLink>
      </nav>

      <UCard>
        <template #header>
          <h2 class="font-semibold">
            Progress
          </h2>
        </template>
        <StageProgress :progress="run.progress" />
      </UCard>

      <UCard>
        <template #header>
          <h2 class="font-semibold">
            Failures ({{ failureTotal }})
          </h2>
        </template>
        <p v-if="!failures.length" class="text-muted">
          No failed jobs.
        </p>
        <div v-else class="overflow-x-auto">
          <UTable :data="failures" :columns="failureColumns">
            <template #error-cell="{ row }">
              <span class="whitespace-normal break-words">{{ row.original.error ?? 'No error recorded' }}</span>
            </template>
          </UTable>
        </div>
        <UPagination v-if="failureTotal > PER_PAGE" v-model:page="failurePage" class="mt-3" :items-per-page="PER_PAGE" :total="failureTotal" />
      </UCard>
    </template>
  </div>
</template>
