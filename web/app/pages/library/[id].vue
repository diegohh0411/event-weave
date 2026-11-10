<script setup lang="ts">
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'
import { apiError } from '../../utils/apiError'
const api = useApi(), route = useRoute()
const document = ref<components['schemas']['LibraryDocumentDetail']>()
const error = ref(''), loading = ref(true)
let generation = 0
watch(() => route.params.id, async id => {
  const mine = ++generation
  loading.value = true; error.value = ''; document.value = undefined
  try {
    const result = await api.GET('/library/documents/{document_id}', { params: { path: { document_id: String(id) } } })
    if (!result.data) throw new Error(apiError(result.error))
    if (mine === generation) document.value = result.data
  } catch (e) { if (mine === generation) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (mine === generation) loading.value = false }
}, { immediate: true })
</script>
<template>
  <div>
    <Crumbs class="mb-4" :items="[{ label: 'Library', to: '/library' }]" />
    <p v-if="loading">Loading document...</p>
    <UAlert v-else-if="error" color="error" title="Could not load document" :description="error" />
    <DocumentReader v-else-if="document" :title="document.title" :outlet="document.outlet" :language="document.language" :byline="document.byline" :published-at="document.published_at" :url="document.url" :text="document.text">
      <template #aside>
        <h2 class="font-bold">Version history</h2>
        <ul class="mt-3 space-y-2 text-sm"><li v-for="version in document.history" :key="version.id"><NuxtLink class="text-primary hover:underline" :to="`/library/${encodeURIComponent(version.id)}`">Version {{ version.version }}</NuxtLink><span v-if="version.superseded_at" class="block text-muted">Superseded {{ new Date(version.superseded_at).toLocaleDateString() }}</span></li></ul>
      </template>
    </DocumentReader>
  </div>
</template>
