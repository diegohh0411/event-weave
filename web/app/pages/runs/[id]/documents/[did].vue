<script setup lang="ts">
import { useApi } from '../../../../api/client'
import type { components } from '../../../../api/schema'
import { sliceCodePoints } from '../../../../utils/highlight'
import { clusterColors } from '../../../../utils/clusterColors'

type Doc = components['schemas']['DocumentOut']
type ClusterOut = components['schemas']['ClusterOut']
interface Piece { text: string, mention?: Doc['mentions'][number] }

const api = useApi()
const route = useRoute()
const id = String(route.params.id)
const did = String(route.params.did)

const errorText = (r: { error?: unknown }) => {
  const detail = (r.error as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string' ? detail : 'the API rejected the request'
}

const doc = ref<Doc>()
const loading = ref(true)
const loadError = ref<string>()
const cards = ref<Record<string, ClusterOut['cluster']['card']>>({})

onMounted(async () => {
  try {
    const r = await api.GET('/runs/{run_id}/documents/{document_id}', { params: { path: { run_id: id, document_id: did } } })
    if (!r.data) throw new Error(errorText(r))
    doc.value = r.data
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
  // The popover shows the cluster card; a card that fails to load just leaves its fields empty.
  const ids = [...new Set((doc.value?.mentions ?? []).map(m => m.cluster_id).filter((c): c is string => !!c))]
  await Promise.all(ids.map(async (cid) => {
    try {
      const r = await api.GET('/runs/{run_id}/clusters/{cluster_id}', { params: { path: { run_id: id, cluster_id: cid } } })
      if (r.data) cards.value = { ...cards.value, [cid]: r.data.cluster.card }
    } catch { /* leave the card empty */ }
  }))
})

const colours = computed(() => clusterColors((doc.value?.mentions ?? []).map(m => m.cluster_id)))

const pieces = computed<Piece[]>(() => {
  const d = doc.value
  if (!d) return []
  const total = Array.from(d.text).length
  const out: Piece[] = []
  let at = 0
  const ordered = [...d.mentions].sort((a, b) => a.start - b.start)
  for (const m of ordered) {
    // Offsets are code points; skip invalid or overlapping ranges so the text always joins back whole.
    if (!(Number.isInteger(m.start) && Number.isInteger(m.end)) || m.start < at || m.end <= m.start || m.end > total) continue
    if (m.start > at) out.push({ text: sliceCodePoints(d.text, at, m.start) })
    out.push({ text: sliceCodePoints(d.text, m.start, m.end), mention: m })
    at = m.end
  }
  if (at < total || !out.length) out.push({ text: sliceCodePoints(d.text, at) })
  return out
})

const fill = (m: Doc['mentions'][number]) => (m.cluster_id ? colours.value.get(m.cluster_id) : undefined)
const cardOf = (m: Doc['mentions'][number]) => (m.cluster_id ? cards.value[m.cluster_id] ?? null : null)
const participants = (card: Record<string, unknown> | null) => ((card?.participants as string[] | undefined) ?? []).join(', ')
const date = (s: string | null) => (s ? s.slice(0, 10) : '—')
</script>

<template>
  <div class="space-y-6">
    <p v-if="loading" class="text-muted">
      Loading document...
    </p>
    <UAlert v-else-if="loadError || !doc" color="error" variant="subtle" title="Could not load the document" :description="loadError" />
    <template v-else>
      <UCard>
        <template #header>
          <h1 class="font-semibold">
            {{ doc.title }}
          </h1>
        </template>
        <dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <dt class="text-sm text-muted">
              Outlet
            </dt>
            <dd>{{ doc.outlet || '—' }}</dd>
          </div>
          <div>
            <dt class="text-sm text-muted">
              Date
            </dt>
            <dd>{{ date(doc.published_at) }}</dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="text-sm text-muted">
              URL
            </dt>
            <dd class="break-all">
              {{ doc.url }}
            </dd>
          </div>
        </dl>
      </UCard>

      <UCard>
        <p data-test="text" class="whitespace-pre-wrap break-words leading-8"><template v-for="(p, i) in pieces" :key="i"><UPopover
          v-if="p.mention"
          mode="hover"
          :open-delay="0"
          :close-delay="150"
        ><span
          data-test="mention"
          class="cursor-help rounded px-0.5 text-black"
          :class="p.mention.cluster_id ? '' : 'border-b-2 border-dashed border-muted'"
          :style="fill(p.mention) ? { backgroundColor: fill(p.mention) } : undefined"
        >{{ p.text }}</span><template #content><div class="space-y-1 p-3 text-sm" data-test="popover">
          <template v-if="p.mention.cluster_id">
            <template v-if="cardOf(p.mention)">
              <p><span class="text-muted">Action</span> {{ cardOf(p.mention)?.action ?? '—' }}</p>
              <p><span class="text-muted">Participants</span> {{ participants(cardOf(p.mention)) || '—' }}</p>
              <p><span class="text-muted">Location</span> {{ cardOf(p.mention)?.location ?? '—' }}</p>
              <p><span class="text-muted">Time</span> {{ cardOf(p.mention)?.time ?? '—' }}</p>
            </template>
            <p v-else class="text-muted">No card for this cluster.</p>
            <NuxtLink :to="`/runs/${id}/clusters/${p.mention.cluster_id}`" class="text-primary hover:underline">
              Cluster of {{ p.mention.cluster_size }} mentions
            </NuxtLink>
          </template>
          <p v-else class="text-muted">Not clustered.</p>
        </div></template></UPopover><template v-else>{{ p.text }}</template></template></p>
      </UCard>
    </template>
  </div>
</template>
