<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'

type RunSummary = components['schemas']['RunSummary']

const api = useApi()
const PER_PAGE = 20
const page = ref(1)
const total = ref(0)
const rows = ref<RunSummary[]>([])
const loading = ref(true)
const error = ref<string>()

async function load() {
  loading.value = true
  try {
    const r = await api.GET('/runs', { params: { query: { offset: (page.value - 1) * PER_PAGE, limit: PER_PAGE } } })
    if (!r.data) throw new Error('could not load runs')
    rows.value = r.data.items
    total.value = r.data.total
    error.value = undefined
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(page, load)

const columns: TableColumn<RunSummary>[] = [
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'track', header: 'Track' },
  { accessorKey: 'state', header: 'State' },
  { accessorKey: 'created_at', header: 'Created' }
]
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-xl font-semibold">
      Runs
    </h1>
    <UAlert v-if="error" color="error" variant="subtle" title="Could not load runs" :description="error" />
    <div class="overflow-x-auto">
      <UTable :data="rows" :columns="columns" :loading="loading">
        <template #name-cell="{ row }">
          <NuxtLink :to="`/runs/${row.original.id}`" class="font-medium text-primary hover:underline">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #created_at-cell="{ row }">
          {{ new Date(row.original.created_at).toLocaleString() }}
        </template>
      </UTable>
    </div>
    <UPagination v-if="total > PER_PAGE" v-model:page="page" :items-per-page="PER_PAGE" :total="total" />
  </div>
</template>
