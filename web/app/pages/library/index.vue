<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'
import { apiError } from '../../utils/apiError'
import { exclusiveEndOfDate } from '../../utils/inclusiveDate'
import { formatChangeBadges } from '../../utils/libraryBadges'

type Document = components['schemas']['LibraryDocument']
const api = useApi(), route = useRoute(), router = useRouter()
const PER_PAGE = 20
const page = ref(1), total = ref(0), rows = ref<Document[]>([]), loading = ref(true), error = ref('')
const facets = ref<components['schemas']['LibraryFacets']>({ outlets: [], languages: [], changes: [], total: 0 })
const array = (v: unknown): string[] => (Array.isArray(v) ? v : v ? [v] : []).filter((x): x is string => typeof x === 'string')
const str = (v: unknown) => typeof v === 'string' ? v : ''
const filters = computed(() => ({ outlets: array(route.query.outlets), languages: array(route.query.languages), published_from: str(route.query.published_from), published_to: str(route.query.published_to), search: str(route.query.search), change: array(route.query.change) as Change[] }))
type Change = 'edited' | 'removed' | 'unchanged'
const CHANGE_LABELS: Record<Change, string> = { edited: 'Edited', removed: 'Removed', unchanged: 'Unchanged' }
const changeItems = computed(() => (['edited', 'removed', 'unchanged'] as const).map(value => { const n = facets.value.changes.find(f => f.value === value)?.count; return { label: n === undefined ? CHANGE_LABELS[value] : `${CHANGE_LABELS[value]} (${n})`, value } }))
function update(key: string, value: string | string[]) {
  page.value = 1
  void router.replace({ query: { ...route.query, [key]: value.length ? value : undefined } })
}
let generation = 0
async function load() {
  const mine = ++generation
  loading.value = true
  const f = filters.value
  const query = { outlets: f.outlets.length ? f.outlets : undefined, languages: f.languages.length ? f.languages : undefined, published_from: f.published_from || undefined, published_to: exclusiveEndOfDate(f.published_to), search: f.search || undefined, change: f.change.length ? f.change : undefined }
  try {
    const [docs, counts] = await Promise.all([
      api.GET('/library/documents', { params: { query: { ...query, offset: (page.value - 1) * PER_PAGE, limit: PER_PAGE } } }),
      api.GET('/library/facets', { params: { query } })
    ])
    if (!docs.data) throw new Error(apiError(docs.error))
    if (!counts.data) throw new Error(apiError(counts.error))
    if (mine !== generation) return
    rows.value = docs.data.items; total.value = docs.data.total; facets.value = counts.data; error.value = ''
  } catch (e) { if (mine === generation) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (mine === generation) loading.value = false }
}
watch([filters, page], load, { immediate: true })
const columns: TableColumn<Document>[] = [
  { accessorKey: 'title', header: 'Title' }, { accessorKey: 'outlet', header: 'Outlet' },
  { accessorKey: 'language', header: 'Language' }, { accessorKey: 'published_at', header: 'Published' }, { accessorKey: 'versions', header: 'Versions' }
]
</script>
<template>
  <div>
    <PageHeader title="Library" description="Browse stored documents." />
    <div class="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
      <UFormField label="Outlets"><USelectMenu class="w-full" multiple value-key="value" :items="facets.outlets.map(f => ({ label: `${f.value} (${f.count})`, value: f.value }))" :model-value="filters.outlets" @update:model-value="v => update('outlets', v as string[])" /></UFormField>
      <UFormField label="Languages"><USelectMenu class="w-full" multiple value-key="value" :items="facets.languages.map(f => ({ label: `${f.value} (${f.count})`, value: f.value }))" :model-value="filters.languages" @update:model-value="v => update('languages', v as string[])" /></UFormField>
      <UFormField label="Published from"><UInput class="w-full" type="date" :model-value="filters.published_from" @update:model-value="v => update('published_from', String(v))" /></UFormField>
      <UFormField label="Published to"><UInput class="w-full" type="date" :model-value="filters.published_to" @update:model-value="v => update('published_to', String(v))" /></UFormField>
      <UFormField label="Change status"><USelectMenu class="w-full" multiple value-key="value" :items="changeItems" :model-value="filters.change" @update:model-value="v => update('change', v as string[])" /></UFormField>
      <UFormField label="Title search"><UInput class="w-full" name="search" :model-value="filters.search" @update:model-value="v => update('search', String(v))" /></UFormField>
    </div>
    <UAlert v-if="error" class="mb-4" color="error" title="Could not load library" :description="error" />
    <p class="mb-3 text-sm text-muted">{{ total }} documents</p>
    <div class="overflow-x-auto rounded-xl border border-default bg-default">
      <UTable :data="rows" :columns="columns" :loading="loading">
        <template #title-cell="{ row }"><NuxtLink class="font-semibold text-primary hover:underline" :to="`/library/${encodeURIComponent(row.original.id)}`">{{ row.original.title }}</NuxtLink><UBadge v-for="badge in formatChangeBadges(row.original)" :key="badge.label" class="ml-2" :color="badge.color" variant="subtle">{{ badge.label }}</UBadge></template>
        <template #published_at-cell="{ row }">{{ row.original.published_at ? new Date(row.original.published_at).toLocaleDateString() : '—' }}</template>
        <template #empty>No documents found.</template>
      </UTable>
    </div>
    <UPagination v-if="total > PER_PAGE" v-model:page="page" class="mt-4" :items-per-page="PER_PAGE" :total="total" />
  </div>
</template>
