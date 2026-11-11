<script setup lang="ts">
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'
import { apiError } from '../../utils/apiError'
import { relativeTime } from '../../utils/site'
import { formatChangeBadges } from '../../utils/libraryBadges'
const OUTCOME_COLORS = { unchanged: 'neutral', edited: 'warning', gone: 'error', redirected: 'error', error: 'neutral' } as const
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
        <div v-if="formatChangeBadges(document).length" class="mt-3 flex flex-wrap gap-2"><UBadge v-for="badge in formatChangeBadges(document)" :key="badge.label" :color="badge.color" variant="subtle">{{ badge.label }}</UBadge></div>
        <p v-if="document.removed_reason" class="mt-1 text-sm text-muted">{{ document.removed_reason }}</p>
        <h2 class="mt-6 font-bold">Rechecks</h2>
        <p class="mt-1 text-sm text-muted">{{ document.next_check_at ? `Next check ${relativeTime(document.next_check_at)}` : 'No further checks' }}</p>
        <ul class="mt-3 space-y-2 text-sm"><li v-for="check in document.rechecks" :key="check.checked_at + check.outcome"><span class="text-muted">{{ new Date(check.checked_at).toLocaleDateString() }}</span> <UBadge :color="OUTCOME_COLORS[check.outcome]" variant="subtle">{{ check.outcome }}</UBadge><span v-if="check.http_status" class="text-muted"> HTTP {{ check.http_status }}</span><span v-if="check.final_url" class="block break-all text-muted">→ {{ check.final_url }}</span><NuxtLink v-if="check.outcome === 'edited' && check.document_id" class="block text-primary hover:underline" :to="`/library/${encodeURIComponent(check.document_id)}`">View new version</NuxtLink></li><li v-if="!document.rechecks.length" class="text-muted">Not rechecked yet.</li></ul>
      </template>
    </DocumentReader>
  </div>
</template>
