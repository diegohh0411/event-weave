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
  <div>
    <Crumbs class="mb-5" :items="[{ label: 'Crawls', to: '/crawls' }, { label: 'Crawl results', to: `/crawls/${route.params.id}` }]" />
    <p v-if="loading" class="text-muted">Loading document...</p>
    <UAlert v-else-if="error" color="error" variant="subtle" title="Could not load document" :description="error" />
    <DocumentReader
      v-else-if="doc"
      :title="doc.title"
      :outlet="doc.outlet"
      :byline="doc.byline"
      :published-at="doc.published_at"
      :url="doc.url"
      :language="doc.language"
      :text="doc.text"
    />
  </div>
</template>
