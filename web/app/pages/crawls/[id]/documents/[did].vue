<script setup lang="ts">
import { useApi } from '../../../../api/client'
import type { components } from '../../../../api/schema'
import { apiError } from '../../../../utils/apiError'
const route = useRoute()
const api = useApi()
const doc = ref<components['schemas']['CrawlDocumentOut']>()
const error = ref('')
const loading = ref(true)
let generation = 0
async function load() {
  const mine = ++generation
  const id = String(route.params.id), did = String(route.params.did)
  loading.value = true; doc.value = undefined; error.value = ''
  try {
    const r = await api.GET('/crawls/{crawl_id}/documents/{document_id}', { params: { path: { crawl_id: id, document_id: did } } })
    if (!r.data) throw new Error(apiError(r.error))
    if (mine === generation) doc.value = r.data
  } catch (e) { if (mine === generation) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (mine === generation) loading.value = false }
}
onMounted(load)
watch(() => [route.params.id, route.params.did], load)
onUnmounted(() => { generation++ })
</script>
<template>
  <div class="space-y-6">
    <NuxtLink :to="`/crawls/${route.params.id}`" class="text-primary hover:underline">← Crawl results</NuxtLink>
    <p v-if="loading">Loading document...</p>
    <UAlert v-else-if="error" color="error" title="Could not load document" :description="error" />
    <template v-else-if="doc">
      <h1 class="text-xl font-semibold">{{ doc.title }}</h1>
      <p>{{ doc.outlet }} · {{ doc.byline }} · {{ doc.published_at ?? 'Date unknown' }}</p>
      <p class="break-all">{{ doc.url }}</p>
      <p class="whitespace-pre-wrap break-words leading-8">{{ doc.text }}</p>
    </template>
  </div>
</template>
