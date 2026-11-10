<script setup lang="ts">
import type { components } from '../api/schema'
import { useApi } from '../api/client'
import { apiError } from '../utils/apiError'
import { validateSite } from '../utils/site'
type Site = components['schemas']['SiteOut']
const props = defineProps<{ site?: Site }>()
const api = useApi()
const draft = reactive({ name: props.site?.name ?? '', domain: props.site?.domain ?? '', language: props.site?.language ?? 'en', feeds: props.site?.feeds?.join('\n') ?? '', sitemaps: props.site?.sitemaps?.join('\n') ?? '', per_day: props.site?.per_day ?? 3, check_every_minutes: props.site?.check_every_minutes ?? 60, daily_cap: props.site?.daily_cap ?? 50, min_interval_seconds: props.site?.min_interval_seconds ?? 2, max_parallel: props.site?.max_parallel ?? 1, enabled: props.site?.enabled ?? true })
const error = ref(''); const saving = ref(false)
const lines = (v: string) => v.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
async function submit() {
  error.value = validateSite({ ...draft, feeds: lines(draft.feeds), sitemaps: lines(draft.sitemaps) })
  if (error.value) return
  saving.value = true
  try {
    const body = { ...draft, name: draft.name.trim(), domain: draft.domain.trim(), feeds: lines(draft.feeds), sitemaps: lines(draft.sitemaps) }
    const result = props.site ? await api.PATCH('/sites/{site_id}', { params: { path: { site_id: props.site.id } }, body }) : await api.POST('/sites', { body })
    if (!result.data) throw new Error(result.response.status === 409 ? 'A site with this name or domain already exists.' : apiError(result.error))
    await navigateTo(`/sites/${result.data.id}`)
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { saving.value = false }
}
</script>
<template>
  <form class="max-w-2xl space-y-4" @submit.prevent="submit">
    <label class="block">Name<input v-model="draft.name" class="ew-field" required></label>
    <label class="block">Domain<input v-model="draft.domain" class="ew-field" required placeholder="example.com"></label>
    <label class="block">Language<select v-model="draft.language" class="ew-field"><option value="en">English</option><option value="es">Spanish</option><option value="hi">Hindi</option><option value="tr">Turkish</option><option value="uk">Ukrainian</option></select></label>
    <label class="block">Sitemaps<textarea v-model="draft.sitemaps" class="ew-field" /><span class="text-sm text-muted">Optional, one URL per line.</span></label>
    <label class="block">Feeds<textarea v-model="draft.feeds" class="ew-field" /><span class="text-sm text-muted">Optional, one URL per line.</span></label>
    <label class="block">Articles per day<input v-model.number="draft.per_day" type="number" min="1" max="200" class="ew-field"><span class="text-sm text-muted">How many articles to look for each day.</span></label>
    <label class="block">Check every (minutes)<input v-model.number="draft.check_every_minutes" type="number" min="5" required class="ew-field"><span class="text-sm text-muted">How often to check this site.</span></label>
    <label class="block">Daily cap<input v-model.number="draft.daily_cap" type="number" min="1" required class="ew-field"><span class="text-sm text-muted">The most articles to fetch per day.</span></label>
    <label class="block">Minimum interval (seconds)<input v-model.number="draft.min_interval_seconds" type="number" min="0.5" step="any" required class="ew-field"><span class="text-sm text-muted">Wait at least this long between requests.</span></label>
    <label class="block">Max parallel<input v-model.number="draft.max_parallel" type="number" min="1" max="4" required class="ew-field"><span class="text-sm text-muted">The most requests to run at once.</span></label>
    <label class="flex gap-2"><input v-model="draft.enabled" type="checkbox"> Enabled</label><p class="text-sm text-muted">Enabled sites are checked automatically.</p>
    <UAlert v-if="error" color="error" :description="error" /><UButton type="submit" :loading="saving">Save site</UButton>
  </form>
</template>
