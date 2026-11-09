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
  <div class="space-y-6">
    <h1 class="text-xl font-semibold">Crawls</h1>
    <p>Collect articles without configuring inference. Discovery uses feeds and sitemaps, not arbitrary links.</p>
    <form class="space-y-4" @submit.prevent="submit">
      <UCard v-for="(site, i) in sites" :key="i">
        <fieldset :disabled="submitting" class="space-y-3">
          <legend class="font-semibold">Site {{ i + 1 }}</legend>
          <div class="grid gap-3 sm:grid-cols-3">
            <label>Domain or site URL <input v-model="site.domain" :name="`domain-${i}`" class="block w-full rounded border border-default p-2" required placeholder="example.com"></label>
            <label>Language <select v-model="site.language" :name="`language-${i}`" class="block w-full rounded border border-default p-2"><option value="en">English</option><option value="es">Spanish</option><option value="hi">Hindi</option><option value="tr">Turkish</option><option value="uk">Ukrainian</option></select></label>
            <label>Max articles per day <input v-model.number="site.per_day" :name="`per-day-${i}`" type="number" min="1" max="200" required class="block w-full rounded border border-default p-2"></label>
          </div>
          <details><summary>Advanced sources</summary><p class="text-sm text-muted">One public HTTP(S) URL per line; explicit sources override automatic discovery.</p>
            <label>Feed URLs <textarea v-model="site.feeds" :name="`feeds-${i}`" class="block w-full rounded border border-default p-2" /></label>
            <label>Sitemap URLs <textarea v-model="site.sitemaps" :name="`sitemaps-${i}`" class="block w-full rounded border border-default p-2" /></label>
          </details>
          <UButton v-if="sites.length > 1" type="button" variant="outline" @click="sites.splice(i, 1)">Remove site</UButton>
        </fieldset>
      </UCard>
      <label>Lookback days <input v-model.number="days" name="days" type="number" min="1" max="30" required class="ml-2 rounded border border-default p-2"></label>
      <div class="flex gap-3"><UButton type="button" variant="outline" :disabled="sites.length >= 20 || submitting" @click="sites.push(newSite())">Add site</UButton><UButton type="submit" :loading="submitting" :disabled="submitting">Start crawl</UButton></div>
      <UAlert v-if="error" color="error" title="Could not start crawl" :description="error" />
    </form>
    <section class="space-y-3"><h2 class="text-lg font-semibold">Recent crawls</h2>
      <UAlert v-if="historyError" color="error" title="Could not load crawl history" :description="historyError" />
      <p v-else-if="!history.length">No crawls yet.</p>
      <ul v-else class="space-y-2"><li v-for="crawl in history" :key="crawl.id"><NuxtLink :to="`/crawls/${crawl.id}`" class="text-primary hover:underline">{{ crawl.request.sites.map(s => s.domain).join(', ') }} — {{ crawl.status }}</NuxtLink> ({{ crawl.counters.fetched }} fetched, {{ crawl.counters.stored }} stored)</li></ul>
      <UPagination v-if="total > pageSize" v-model:page="page" :items-per-page="pageSize" :total="total" />
    </section>
  </div>
</template>
