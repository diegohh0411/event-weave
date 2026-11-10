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
    try { await Promise.all([refresh(), loadFailures()]) } catch { /* keep the last good state; the next tick retries */ } finally { polling = false }
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
const failuresError = ref<string>()
async function loadFailures() {
  try {
    const r = await api.GET('/runs/{run_id}/failures', { params: { path: { run_id: id }, query: { offset: (failurePage.value - 1) * PER_PAGE, limit: PER_PAGE } } })
    if (r.data) {
      failures.value = r.data.items
      failureTotal.value = r.data.total
      failuresError.value = undefined
    } else {
      const detail = (r.error as { detail?: unknown } | undefined)?.detail
      failuresError.value = typeof detail === 'string' ? detail : 'the API rejected the request'
    }
  } catch (e) {
    failuresError.value = e instanceof Error ? e.message : String(e)
  }
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
  <div>
    <Crumbs class="mb-4" :items="[{ label: 'Runs', to: '/runs' }]" />
    <p v-if="loading" class="text-muted">
      Loading run...
    </p>
    <UAlert v-else-if="loadError || !run" color="error" variant="subtle" title="Could not load the run" :description="loadError" />
    <template v-else>
      <PageHeader :title="run.name">
        <template #meta>
          <p class="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted">
            <StatusBadge :status="run.state" />
            <span>{{ run.track }} track</span>
          </p>
        </template>
        <template #actions>
          <UButton v-if="action" size="lg" :color="action === 'cancel' ? 'error' : 'primary'" :variant="action === 'cancel' ? 'outline' : 'solid'" :loading="acting" :disabled="acting" @click="act">
            {{ LABEL[action] }}
          </UButton>
        </template>
      </PageHeader>
      <RunNav :run-id="id" />

      <div class="grid gap-8 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <dl class="grid h-fit grid-cols-2 gap-px overflow-hidden rounded-xl border border-default bg-[var(--ui-border-muted)] lg:grid-cols-1">
          <div class="bg-default p-5">
            <dt class="text-sm text-muted">
              Dollars so far
            </dt>
            <dd class="mt-1 text-2xl font-bold tracking-tight text-highlighted">
              <span class="tabular">${{ run.dollars }}</span><span v-if="run.dollars_estimated" class="ml-2 text-sm font-normal text-muted">estimated</span>
            </dd>
          </div>
          <div class="bg-default p-5">
            <dt class="text-sm text-muted">
              Mentions per hour
            </dt>
            <dd class="tabular mt-1 text-2xl font-bold tracking-tight text-highlighted">
              {{ run.mentions_per_hour === null ? '—' : Math.round(run.mentions_per_hour) }}
            </dd>
          </div>
        </dl>

        <section class="min-w-0 rounded-xl border border-default bg-default p-5">
          <h2 class="mb-4 text-base font-bold text-highlighted">
            Progress
          </h2>
          <StageProgress :progress="run.progress" />
        </section>
      </div>

      <section class="mt-8 space-y-3">
        <h2 class="text-base font-bold text-highlighted">
          Failures <span class="tabular font-normal text-muted">({{ failureTotal }})</span>
        </h2>
        <UAlert v-if="failuresError" color="error" variant="subtle" :title="`Could not load failures: ${failuresError}`" />
        <p v-else-if="!failures.length" class="rounded-xl border border-dashed border-accented p-5 text-sm text-muted">
          No failed jobs.
        </p>
        <div v-if="failures.length" class="overflow-x-auto rounded-xl border border-default bg-default">
          <UTable :data="failures" :columns="failureColumns" :ui="{ th: 'font-medium text-muted' }">
            <template #error-cell="{ row }">
              <span class="whitespace-normal break-words">{{ row.original.error ?? 'No error recorded' }}</span>
            </template>
          </UTable>
        </div>
        <UPagination v-if="failureTotal > PER_PAGE" v-model:page="failurePage" :items-per-page="PER_PAGE" :total="failureTotal" />
      </section>
    </template>
  </div>
</template>
