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
const robots = computed(() => Object.entries(crawl.value?.counters.reasons ?? {}).filter(([reason]) => /robots/i.test(reason)).reduce((n, [, count]) => n + count, 0))
</script>
<template>
  <div class="space-y-6">
    <NuxtLink to="/crawls" class="text-primary hover:underline">← Crawls</NuxtLink>
    <p v-if="loading">Loading crawl...</p>
    <div v-else-if="error" class="space-y-2"><UAlert color="error" title="Could not load crawl" :description="error" /><UButton type="button" variant="outline" @click="load">Retry</UButton></div>
    <template v-else-if="crawl">
      <h1 class="text-xl font-semibold">Crawl {{ crawl.id }}</h1>
      <p>Status: <strong>{{ crawl.status }}</strong> — {{ crawl.request.sites.map(s => s.domain).join(', ') }}</p>
      <section aria-label="Crawl settings" class="space-y-1">
        <p>Lookback: {{ crawl.request.days }} {{ crawl.request.days === 1 ? 'day' : 'days' }}</p>
        <p v-for="site in crawl.request.sites" :key="site.domain">{{ site.domain }} — maximum {{ site.per_day }} articles per day</p>
      </section>
      <p>{{ crawl.counters.discovered }} discovered, {{ crawl.counters.fetched }} fetched, {{ crawl.counters.stored }} stored, {{ crawl.counters.existing }} existing, {{ crawl.counters.duplicates }} duplicates, {{ crawl.counters.rejected }} rejected, {{ crawl.counters.errors }} errors</p>
      <UAlert v-if="crawl.error" color="error" title="Crawl failed" :description="crawl.error" />
      <section v-if="crawl.status === 'completed' && crawl.counters.stored === 0" class="space-y-2">
        <h2 class="font-semibold">No new articles stored</h2>
        <p v-if="paywalls">{{ paywalls }} articles skipped: detected paywall.</p>
        <p v-if="robots">{{ robots }} articles blocked by robots rules.</p>
        <p v-if="crawl.counters.existing || crawl.counters.duplicates">Previously stored documents may still be available below.</p>
        <p v-if="!resultsLoading && sources.some(s => s.error)">Some sources failed discovery; try explicit feed or sitemap URLs.</p>
        <p v-if="!resultsLoading && !sourcesError && !sources.length">No sources were found. Try explicit feed or sitemap URLs.</p>
        <p v-if="!resultsLoading && !crawl.counters.discovered && sources.length && !sources.some(s => s.error) && !sourcesError">No articles found in the {{ crawl.request.days }}-day lookback window. Try increasing the lookback period or adding explicit feed/sitemap URLs.</p>
        <p v-if="crawl.counters.discovered && !paywalls && !robots && !crawl.counters.existing && !crawl.counters.duplicates">Check article outcomes and source errors below.</p>
      </section>
      <p v-for="(count, reason) in crawl.counters.reasons" :key="reason">{{ reason }}: {{ count }}</p>
      <NuxtLink v-if="crawl.status === 'completed'" :to="`/?crawl_id=${crawl.id}`" class="inline-block rounded bg-primary px-3 py-2 text-white">Use crawl in builder</NuxtLink>
      <section class="space-y-2"><h2 class="text-lg font-semibold">Sources</h2>
        <p v-if="resultsLoading">Loading sources...</p><p v-else-if="sourcesError" role="alert">Could not load sources: {{ sourcesError }}</p><p v-else-if="!sources.length">No sources recorded.</p>
        <ul v-else class="space-y-2"><li v-for="s in sources" :key="s.url" class="break-all">{{ s.site }} — {{ s.kind }} — {{ s.url }} — {{ s.status }}<span v-if="s.error">: {{ s.error }}</span></li></ul>
      </section>
      <section class="space-y-2"><h2 class="text-lg font-semibold">Article outcomes</h2>
        <p v-if="resultsLoading">Loading articles...</p><p v-else-if="articlesError" role="alert">Could not load articles: {{ articlesError }}</p><p v-else-if="!articles.length">No articles recorded.</p>
        <ul v-else class="space-y-2"><li v-for="a in articles" :key="a.canonical_url" class="break-all">{{ a.outlet }} — {{ a.url }} — {{ a.status }}<span v-if="a.reason">: {{ a.reason }}</span> <NuxtLink v-if="a.document_id" :to="`/crawls/${crawl.id}/documents/${encodeURIComponent(a.document_id)}`" class="text-primary hover:underline">View document</NuxtLink></li></ul>
      </section>
    </template>
  </div>
</template>
