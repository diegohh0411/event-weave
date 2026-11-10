<script setup lang="ts">
import { useApi } from '../../api/client'
import { fetchAll } from '../../api/fetchAll'
import type { components } from '../../api/schema'
import { apiError } from '../../utils/apiError'

type Crawl = components['schemas']['CrawlOut']
type Article = components['schemas']['CrawlArticleOut']
type Source = components['schemas']['CrawlSourceOut']
const api = useApi()
const route = useRoute()
const crawl = ref<Crawl>()
const articles = ref<Article[]>([])
const sources = ref<Source[]>([])
const error = ref('')
const articlesError = ref('')
const sourcesError = ref('')
const loading = ref(true)
const resultsLoading = ref(true)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let disposed = false
function stop() { if (timer) clearTimeout(timer); timer = undefined }
async function load() {
  stop()
  const mine = ++generation
  const id = String(route.params.id)
  resultsLoading.value = true
  const current = () => !disposed && mine === generation && id === String(route.params.id)
  try {
    const r = await api.GET('/crawls/{crawl_id}', { params: { path: { crawl_id: id } } })
    if (!r.data) throw new Error(apiError(r.error))
    if (!current()) return
    crawl.value = r.data; error.value = ''; loading.value = false
    const [a, s] = await Promise.allSettled([
      fetchAll(async (offset, limit) => {
        const result = await api.GET('/crawls/{crawl_id}/articles', { params: { path: { crawl_id: id }, query: { offset, limit } } })
        if (!result.data) throw new Error(apiError(result.error))
        return result.data
      }),
      fetchAll(async (offset, limit) => {
        const result = await api.GET('/crawls/{crawl_id}/sources', { params: { path: { crawl_id: id }, query: { offset, limit } } })
        if (!result.data) throw new Error(apiError(result.error))
        return result.data
      })
    ])
    if (!current()) return
    resultsLoading.value = false
    if (a.status === 'fulfilled') { articles.value = a.value; articlesError.value = '' }
    else { articles.value = []; articlesError.value = String(a.reason) }
    if (s.status === 'fulfilled') { sources.value = s.value; sourcesError.value = '' }
    else { sources.value = []; sourcesError.value = String(s.reason) }
    if (r.data.status === 'queued' || r.data.status === 'running') timer = setTimeout(load, 2000)
  } catch (e) {
    if (current()) { error.value = e instanceof Error ? e.message : String(e); loading.value = false }
  }
}
watch(() => route.params.id, () => { crawl.value = undefined; articles.value = []; sources.value = []; loading.value = true; void load() })
onMounted(load)
onUnmounted(() => { disposed = true; generation++; stop() })
const paywalls = computed(() => Object.entries(crawl.value?.counters.reasons ?? {}).filter(([reason]) => /paywall/i.test(reason)).reduce((n, [, count]) => n + count, 0))
const COUNTERS = ['discovered', 'fetched', 'stored', 'existing', 'duplicates', 'rejected', 'errors'] as const
const active = computed(() => crawl.value?.status === 'queued' || crawl.value?.status === 'running')
const robots = computed(() => Object.entries(crawl.value?.counters.reasons ?? {}).filter(([reason]) => /robots/i.test(reason)).reduce((n, [, count]) => n + count, 0))
</script>
<template>
  <div>
    <Crumbs class="mb-4" :items="[{ label: 'Crawls', to: '/crawls' }]" />
    <p v-if="loading" class="text-muted">Loading crawl...</p>
    <div v-else-if="error" class="space-y-3"><UAlert color="error" variant="subtle" title="Could not load crawl" :description="error" /><UButton type="button" color="neutral" variant="outline" @click="load">Retry</UButton></div>
    <template v-else-if="crawl">
      <PageHeader :title="crawl.request.sites.map(s => s.domain).join(', ')">
        <template #meta>
          <p class="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted">
            <StatusBadge :status="crawl.status" />
            <span>Crawl <span class="break-all">{{ crawl.id }}</span></span>
            <span>{{ crawl.request.days }} {{ crawl.request.days === 1 ? 'day' : 'days' }} back</span>
          </p>
        </template>
        <template #actions>
          <UButton v-if="crawl.status === 'completed'" :to="`/?crawl_id=${crawl.id}`" size="lg">Use crawl in a new run</UButton>
        </template>
      </PageHeader>

      <div class="space-y-8">
        <div class="overflow-hidden rounded-xl border border-default bg-default">
          <UProgress v-if="active" size="2xs" class="rounded-none" />
          <ul class="grid grid-cols-2 gap-px bg-[var(--ui-border-muted)] sm:grid-cols-4 lg:grid-cols-7">
            <li v-for="k in COUNTERS" :key="k" class="bg-default px-4 py-3.5 text-sm text-muted">
              <span class="tabular block text-2xl font-bold tracking-tight" :class="k === 'stored' ? 'text-primary' : k === 'errors' && crawl.counters.errors ? 'text-error' : 'text-highlighted'">{{ crawl.counters[k] }}</span> {{ k }}
            </li>
          </ul>
        </div>

        <UAlert v-if="crawl.error" color="error" variant="subtle" title="Crawl failed" :description="crawl.error" />

        <section v-if="crawl.status === 'completed' && crawl.counters.stored === 0" class="space-y-2 rounded-xl border border-[var(--color-weld-500)]/50 bg-[var(--ew-weld)]/40 p-5 text-sm">
          <h2 class="text-base font-bold text-highlighted">No new articles stored</h2>
          <p v-if="paywalls">{{ paywalls }} articles skipped: detected paywall.</p>
          <p v-if="robots">{{ robots }} articles blocked by robots rules.</p>
          <p v-if="crawl.counters.existing || crawl.counters.duplicates">Previously stored documents may still be available below.</p>
          <p v-if="!resultsLoading && sources.some(s => s.error)">Some sources failed discovery; try explicit feed or sitemap URLs.</p>
          <p v-if="!resultsLoading && !sourcesError && !sources.length">No sources were found. Try explicit feed or sitemap URLs.</p>
          <p v-if="!resultsLoading && !crawl.counters.discovered && sources.length && !sources.some(s => s.error) && !sourcesError">No articles found in this time window. Try a longer lookback or explicit feed/sitemap URLs.</p>
          <p v-if="crawl.counters.discovered && !paywalls && !robots && !crawl.counters.existing && !crawl.counters.duplicates">Check article outcomes and source errors below.</p>
        </section>

        <section v-if="Object.keys(crawl.counters.reasons).length" class="space-y-2">
          <h2 class="text-base font-bold text-highlighted">Why articles were skipped</h2>
          <ul class="flex flex-wrap gap-2">
            <li v-for="(count, reason) in crawl.counters.reasons" :key="reason" class="rounded-full border border-default bg-default px-3 py-1 text-sm">{{ reason }}: <span class="tabular font-semibold text-highlighted">{{ count }}</span></li>
          </ul>
        </section>

        <section class="space-y-3">
          <h2 class="text-base font-bold text-highlighted">Articles</h2>
          <p v-if="resultsLoading" class="text-sm text-muted">Loading articles...</p><p v-else-if="articlesError" role="alert" class="text-sm text-error">Could not load articles: {{ articlesError }}</p><p v-else-if="!articles.length" class="text-sm text-muted">No articles recorded.</p>
          <div v-else class="overflow-x-auto rounded-xl border border-default bg-default">
            <table class="w-full text-left text-sm">
              <thead class="border-b border-default text-muted"><tr><th class="px-4 py-2.5 font-medium">Article</th><th class="px-4 py-2.5 font-medium">Outcome</th><th class="px-4 py-2.5"><span class="sr-only">Document</span></th></tr></thead>
              <tbody class="divide-y divide-default">
                <tr v-for="a in articles" :key="a.canonical_url">
                  <td class="max-w-0 px-4 py-3"><span class="block font-medium text-highlighted">{{ a.outlet }}</span><a :href="a.url" target="_blank" rel="noopener noreferrer" class="block truncate text-muted hover:text-highlighted hover:underline">{{ a.url }}</a></td>
                  <td class="px-4 py-3 align-top"><StatusBadge :status="a.status" /><span v-if="a.reason" class="mt-1 block text-xs text-muted">{{ a.reason }}</span></td>
                  <td class="px-4 py-3 text-right align-top"><NuxtLink v-if="a.document_id" :to="`/crawls/${crawl.id}/documents/${encodeURIComponent(a.document_id)}`" class="whitespace-nowrap font-semibold text-primary hover:underline">Read</NuxtLink></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="space-y-3">
          <h2 class="text-base font-bold text-highlighted">Sources</h2>
          <p v-if="resultsLoading" class="text-sm text-muted">Loading sources...</p><p v-else-if="sourcesError" role="alert" class="text-sm text-error">Could not load sources: {{ sourcesError }}</p><p v-else-if="!sources.length" class="text-sm text-muted">No sources recorded.</p>
          <div v-else class="overflow-x-auto rounded-xl border border-default bg-default">
            <table class="w-full text-left text-sm">
              <thead class="border-b border-default text-muted"><tr><th class="px-4 py-2.5 font-medium">Source</th><th class="px-4 py-2.5 font-medium">Kind</th><th class="px-4 py-2.5 font-medium">Status</th></tr></thead>
              <tbody class="divide-y divide-default">
                <tr v-for="s in sources" :key="s.url">
                  <td class="max-w-0 px-4 py-3"><span class="block font-medium text-highlighted">{{ s.site }}</span><span class="block truncate text-muted">{{ s.url }}</span></td>
                  <td class="px-4 py-3 align-top">{{ s.kind }}</td>
                  <td class="px-4 py-3 align-top"><StatusBadge :status="s.status" /><span v-if="s.error" class="mt-1 block text-xs text-error">{{ s.error }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>
