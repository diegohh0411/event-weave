<script setup lang="ts">
import type { components } from '../../../api/schema'
import { useApi } from '../../../api/client'
import { apiError } from '../../../utils/apiError'
import { formatChangeReport, relativeTime, backfillPercent, formatBackfillEta } from '../../../utils/site'
const api = useApi(); const route = useRoute(); const toast = useToast(); const id = String(route.params.id); const page = ref(1); const size = 20
const { data: site, error, refresh } = await useAsyncData(`site-${id}`, async () => { const r = await api.GET('/sites/{site_id}', { params: { path: { site_id: id } } }); if (!r.data) throw new Error(apiError(r.error)); return r.data })
const showBackfill = ref(false)
const { data: history, error: historyError, status: historyStatus, refresh: refreshHistory } = await useAsyncData(`site-history-${id}`, async () => {
  const triggers = showBackfill.value ? [undefined] : ['adhoc', 'manual', 'schedule'] as const
  const pages = await Promise.all(triggers.map(async trigger => {
    const items: components['schemas']['CrawlOut'][] = []
    let total = 0
    do {
      const r = await api.GET('/sites/{site_id}/crawls', { params: { path: { site_id: id }, query: { trigger, offset: items.length, limit: Math.min(page.value * size - items.length, 200) } } })
      if (!r.data) throw new Error(apiError(r.error))
      total = r.data.total
      items.push(...r.data.items)
      if (!r.data.items.length) break
    } while (items.length < Math.min(total, page.value * size))
    return { items, total }
  }))
  const all = showBackfill.value ? undefined : await api.GET('/sites/{site_id}/crawls', { params: { path: { site_id: id }, query: { offset: 0, limit: 1 } } })
  if (all && !all.data) throw new Error(apiError(all.error))
  const items = pages.flatMap(p => p.items).sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))
  return { items: items.slice((page.value - 1) * size, page.value * size), total: pages.reduce((n, p) => n + p.total, 0), allTotal: all?.data?.total ?? pages[0]!.total }
}, { watch: [page, showBackfill] })
watch(showBackfill, () => { page.value = 1 })
const { data: backfill, error: backfillError, refresh: refreshBackfill } = await useAsyncData(`backfill-${id}`, async () => {
  const r = await api.GET('/sites/{site_id}/backfill', { params: { path: { site_id: id } } })
  if (!r.data) throw new Error(apiError(r.error))
  return r.data
})
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { if (backfill.value?.state === 'running') { refreshBackfill(); refreshHistory() } }, 10000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
async function control(action: 'start' | 'pause' | 'resume' | 'cancel') {
  if (action === 'cancel' && !confirm('Cancel backfill and clear its progress?')) return
  const r = await api.POST('/sites/{site_id}/backfill', { params: { path: { site_id: id } }, body: { action } })
  if (!r.data) toast.add({ title: 'Could not update backfill', description: apiError(r.error), color: 'error' })
  else { backfill.value = r.data; await refresh() }
}
async function check() { const r = await api.POST('/sites/{site_id}/check', { params: { path: { site_id: id } } }); toast.add({ title: r.data ? 'Check started' : r.response.status === 409 ? 'A check is already running' : 'Could not start check', color: r.data ? 'success' : 'error' }) }
async function toggle() { if (!site.value) return; const r = await api.PATCH('/sites/{site_id}', { params: { path: { site_id: id } }, body: { enabled: !site.value.enabled } }); if (!r.data) toast.add({ title: 'Could not update site', description: apiError(r.error), color: 'error' }); else await refresh() }
async function remove() { if (!confirm('Delete this site?')) return; const r = await api.DELETE('/sites/{site_id}', { params: { path: { site_id: id } } }); if (!r.response.ok) toast.add({ title: 'Could not delete site', description: apiError(r.error), color: 'error' }); else await navigateTo('/sites') }
</script>
<template><div><Crumbs class="mb-4" :items="[{ label: 'Sites', to: '/sites' }]" /><UAlert v-if="error" color="error" :description="error.message" /><template v-else-if="site"><PageHeader :title="site.name || site.domain" :description="site.domain"><template #actions><UButton :to="`/sites/${id}/edit`" variant="outline">Edit</UButton><UButton @click="check">Check now</UButton><UButton color="neutral" variant="outline" @click="toggle">{{ site.enabled ? 'Disable' : 'Enable' }}</UButton><UTooltip :text="historyError ? 'Unable to determine whether this site has crawls' : 'Sites with crawls cannot be deleted; disable them instead'" :disabled="!!history && !history.allTotal"><span><UButton color="error" variant="outline" :disabled="!history || history.allTotal > 0" @click="remove">Delete</UButton></span></UTooltip></template></PageHeader>
<section class="space-y-2"><h2 class="font-bold">Settings</h2><p>Language: {{ site.language }} · {{ site.enabled ? 'Enabled' : 'Disabled' }} · Every {{ site.check_every_minutes }} min · Daily cap: {{ site.daily_cap }}</p><p>Articles per day: {{ site.per_day }} · Minimum interval: {{ site.min_interval_seconds }} seconds · Max parallel: {{ site.max_parallel }}</p><p>Sitemaps: {{ site.sitemaps?.join(', ') || 'Automatic' }}</p><p>Feeds: {{ site.feeds?.join(', ') || 'Automatic' }}</p></section>
<section class="mt-8 rounded-xl border border-default p-5 space-y-3"><h2 class="font-bold">Backfill <UBadge v-if="backfill" color="neutral">{{ backfill.state }}</UBadge></h2>
<UAlert v-if="backfillError" color="error" :description="backfillError.message" />
<template v-if="backfill"><p v-if="backfill.error" class="text-error">{{ backfill.error }}</p><div class="flex gap-2"><UButton v-if="['off', 'done', 'failed'].includes(backfill.state)" :disabled="backfill.mode === 'off' || !site.enabled" @click="control('start')">Start</UButton><UButton v-if="backfill.state === 'running'" @click="control('pause')">Pause</UButton><UButton v-if="backfill.state === 'paused'" :disabled="!site.enabled" @click="control('resume')">Resume</UButton><UButton v-if="['running', 'paused'].includes(backfill.state)" color="error" variant="outline" @click="control('cancel')">Cancel</UButton></div>
<progress class="w-full" :value="backfill.urls.done" :max="backfill.urls.found || 1" /><p>{{ backfill.urls.done }} / {{ backfill.urls.found }} URLs ({{ backfillPercent(backfill.urls.done, backfill.urls.found) }}%)</p><p>Sitemaps scanned: {{ backfill.sitemaps.done + backfill.sitemaps.error }} / {{ backfill.sitemaps.total }} · Stored: {{ backfill.urls.stored }} · Already had: {{ backfill.urls.existing }} · Rejected: {{ backfill.urls.rejected }} · Errors: {{ backfill.urls.error }}</p><p v-if="backfill.oldest_reached">Reached back to {{ new Date(backfill.oldest_reached).toLocaleDateString() }}</p><p>{{ backfill.fetched_last_hour }} fetched in the last hour (limit {{ backfill.per_hour }}/h) · {{ formatBackfillEta(backfill.eta) }}</p></template></section>
<section class="mt-8 space-y-3"><h2 class="font-bold">Check history</h2><label class="flex gap-2"><input v-model="showBackfill" type="checkbox"> Show backfill batches</label><UAlert v-if="historyError" color="error" :description="historyError.message" /><p v-else-if="historyStatus === 'pending' || !history">Loading checks…</p><p v-else-if="!history.items.length">No checks yet.</p><table v-else class="w-full text-left text-sm"><thead><tr><th scope="col">Date</th><th scope="col">Status</th><th scope="col">Trigger</th><th scope="col">Changes</th></tr></thead><tbody><tr v-for="crawl in history.items" :key="crawl.id"><td class="py-3"><NuxtLink class="text-primary" :to="`/crawls/${crawl.id}`">{{ relativeTime(crawl.created_at) }}</NuxtLink></td><td><StatusBadge :status="crawl.status" /></td><td><UBadge color="neutral">{{ crawl.trigger }}</UBadge></td><td>{{ formatChangeReport(crawl.counters) }}</td></tr></tbody></table><UPagination v-if="history && history.total > size" v-model:page="page" :items-per-page="size" :total="history.total" /></section></template></div></template>
