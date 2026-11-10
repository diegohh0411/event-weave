<script setup lang="ts">
import { useApi } from '../../api/client'
import type { components } from '../../api/schema'
import { apiError } from '../../utils/apiError'

type Site = components['schemas']['CrawlSite']
type Crawl = components['schemas']['CrawlOut']
type Draft = { domain: string, language: Site['language'], per_day: number, feeds: string, sitemaps: string }
const api = useApi()
const newSite = (): Draft => ({ domain: '', language: 'en', per_day: 3, feeds: '', sitemaps: '' })
const sites = ref<Draft[]>([newSite()])
const days = ref(1)
const submitting = ref(false)
const error = ref('')
const historyError = ref('')
const history = ref<Crawl[]>([])
const page = ref(1)
const total = ref(0)
const pageSize = 20
let historyGeneration = 0
let disposed = false
async function loadHistory() {
  const mine = ++historyGeneration
  try {
    const r = await api.GET('/crawls', { params: { query: { offset: (page.value - 1) * pageSize, limit: pageSize } } })
    if (!r.data) throw new Error(apiError(r.error))
    if (disposed || mine !== historyGeneration) return
    history.value = r.data.items; total.value = r.data.total; historyError.value = ''
  } catch (e) { if (!disposed && mine === historyGeneration) historyError.value = e instanceof Error ? e.message : String(e) }
}
onMounted(loadHistory)
watch(page, loadHistory)
onUnmounted(() => { disposed = true; historyGeneration++ })
const lines = (text: string) => text.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
function domainInput(value: string): string {
  const input = value.trim()
  if (!input.includes('://')) return input
  try {
    const u = new URL(input)
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.port || (u.pathname !== '/' && u.pathname !== '') || u.search || u.hash) throw new Error()
    return u.hostname
  } catch { throw new Error('Enter a domain or an HTTP(S) site URL without a path, credentials or nonstandard port.') }
}
async function submit() {
  if (submitting.value) return
  error.value = ''
  try {
    const body: components['schemas']['CrawlRequest'] = { days: Number(days.value), sites: sites.value.map(s => ({
      domain: domainInput(s.domain), language: s.language, per_day: Number(s.per_day), feeds: lines(s.feeds), sitemaps: lines(s.sitemaps)
    })) }
    if (!Number.isInteger(body.days) || body.days < 1 || body.days > 30 || body.sites.some(s => !s.domain || !Number.isInteger(s.per_day) || s.per_day < 1 || s.per_day > 200)) throw new Error('Enter domains, 1–30 days and 1–200 articles per site per day.')
    submitting.value = true
    const r = await api.POST('/crawls', { body })
    if (!r.data) throw new Error(apiError(r.error))
    if (!disposed) await navigateTo(`/crawls/${r.data.id}`)
  } catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : String(e) }
  finally { submitting.value = false }
}
</script>

<template>
  <div>
    <PageHeader title="Crawls" description="Collect recent articles from news sites. Discovery follows each site's feeds and sitemaps, never arbitrary links." />

    <div class="grid gap-8 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <form class="min-w-0 space-y-4" @submit.prevent="submit">
        <h2 class="text-base font-bold text-highlighted">New crawl</h2>
        <div v-for="(site, i) in sites" :key="i" class="rounded-xl border border-default bg-default p-5">
          <fieldset :disabled="submitting" class="space-y-4">
            <legend class="sr-only">Site {{ i + 1 }}</legend>
            <div class="flex items-center justify-between gap-3">
              <span aria-hidden="true" class="text-sm font-semibold text-highlighted">Site {{ i + 1 }}</span>
              <UButton v-if="sites.length > 1" type="button" color="neutral" variant="ghost" size="xs" @click="sites.splice(i, 1)">Remove site</UButton>
            </div>
            <div class="grid gap-4 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
              <label class="space-y-1.5 text-sm font-medium text-highlighted">Domain or site URL <input v-model="site.domain" :name="`domain-${i}`" class="ew-field font-normal" required placeholder="example.com"></label>
              <label class="space-y-1.5 text-sm font-medium text-highlighted">Language <select v-model="site.language" :name="`language-${i}`" class="ew-field font-normal"><option value="en">English</option><option value="es">Spanish</option><option value="hi">Hindi</option><option value="tr">Turkish</option><option value="uk">Ukrainian</option></select></label>
              <label class="space-y-1.5 text-sm font-medium text-highlighted">Articles per day <input v-model.number="site.per_day" :name="`per-day-${i}`" type="number" min="1" max="200" required class="ew-field tabular font-normal"></label>
            </div>
            <details class="group rounded-md bg-muted px-3 py-2">
              <summary class="cursor-pointer select-none text-sm font-medium text-muted group-open:text-highlighted">Feeds and sitemaps</summary>
              <div class="mt-3 space-y-3 pb-1">
                <p class="text-sm text-muted">One public HTTP(S) URL per line. Sources you list replace automatic discovery.</p>
                <div class="grid gap-3 sm:grid-cols-2">
                  <label class="space-y-1.5 text-sm font-medium text-highlighted">Feed URLs <textarea v-model="site.feeds" :name="`feeds-${i}`" rows="3" class="ew-field font-normal" /></label>
                  <label class="space-y-1.5 text-sm font-medium text-highlighted">Sitemap URLs <textarea v-model="site.sitemaps" :name="`sitemaps-${i}`" rows="3" class="ew-field font-normal" /></label>
                </div>
              </div>
            </details>
          </fieldset>
        </div>
        <UButton type="button" color="neutral" variant="outline" block class="border-dashed" :disabled="sites.length >= 20 || submitting" @click="sites.push(newSite())">Add site</UButton>
        <div class="flex flex-wrap items-end justify-between gap-4 border-t border-default pt-4">
          <label class="flex items-center gap-3 text-sm font-medium text-highlighted">Look back <input v-model.number="days" name="days" type="number" min="1" max="30" required class="ew-field tabular w-20 font-normal"> days</label>
          <UButton type="submit" size="lg" :loading="submitting" :disabled="submitting">Start crawl</UButton>
        </div>
        <UAlert v-if="error" color="error" variant="subtle" title="Could not start crawl" :description="error" />
      </form>

      <section class="min-w-0 space-y-3">
        <h2 class="text-base font-bold text-highlighted">Recent crawls</h2>
        <UAlert v-if="historyError" color="error" variant="subtle" title="Could not load crawl history" :description="historyError" />
        <p v-else-if="!history.length" class="rounded-xl border border-dashed border-accented p-5 text-sm text-muted">No crawls yet. Add a site and start your first crawl.</p>
        <ul v-else class="divide-y divide-default overflow-hidden rounded-xl border border-default bg-default">
          <li v-for="crawl in history" :key="crawl.id">
            <NuxtLink :to="`/crawls/${crawl.id}`" class="block space-y-1.5 px-4 py-3 hover:bg-muted">
              <span class="flex items-start justify-between gap-3">
                <span class="min-w-0 break-words text-sm font-semibold text-highlighted">{{ crawl.request.sites.map(s => s.domain).join(', ') }}</span>
                <StatusBadge :status="crawl.status" />
              </span>
              <span class="block text-xs text-muted"><span class="tabular">{{ crawl.counters.fetched }}</span> fetched, <span class="tabular">{{ crawl.counters.stored }}</span> stored</span>
            </NuxtLink>
          </li>
        </ul>
        <UPagination v-if="total > pageSize" v-model:page="page" :items-per-page="pageSize" :total="total" />
      </section>
    </div>
  </div>
</template>
