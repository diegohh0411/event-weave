<script setup lang="ts">
import { useApi } from '../../../../api/client'
import type { components } from '../../../../api/schema'
import { sliceCodePoints } from '../../../../utils/highlight'
import { clusterColors } from '../../../../utils/clusterColors'
import { cardFields } from '../../../../utils/clusterCard'

type Doc = components['schemas']['DocumentOut']
type Mention = Doc['mentions'][number]
type ClusterOut = components['schemas']['ClusterOut']
interface Piece { text: string, mention?: Mention }

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
  // The popover and the legend show the cluster card; a card that fails to load just leaves its fields empty.
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

const fill = (m: Mention) => (m.cluster_id ? colours.value.get(m.cluster_id) : undefined)
const cardOf = (cid: string | null) => {
  const card = cid ? cards.value[cid] : null
  return card ? cardFields(card) : null
}

// The events in this document, in reading order, with how often each appears here.
const events = computed(() => {
  const shown = pieces.value.flatMap(p => (p.mention ? [p.mention] : []))
  const byCluster = new Map<string, { id: string, colour: string, here: number, size: number | null }>()
  for (const m of shown) {
    if (!m.cluster_id) continue
    const e = byCluster.get(m.cluster_id)
    if (e) e.here++
    else byCluster.set(m.cluster_id, { id: m.cluster_id, colour: colours.value.get(m.cluster_id)!, here: 1, size: m.cluster_size })
  }
  return { clusters: [...byCluster.values()], unclustered: shown.filter(m => !m.cluster_id).length }
})

// Hovering a mention, a thread or a legend entry lights that event up; clicking a legend entry pins it.
const hovered = ref<string | null>(null)
const pinned = ref<string | null>(null)
const focused = computed(() => hovered.value ?? pinned.value)
const mentionClass = (m: Mention) => {
  if (!m.cluster_id) return 'border-b-2 border-dashed border-muted'
  if (!focused.value) return 'ew-mention'
  return m.cluster_id === focused.value ? 'ew-mention is-lit' : 'ew-mention is-dim'
}

// The thread rail: one vertical thread per event, knotted at the height of each of its mentions.
const MAX_LANES = 6
const LANE_GAP = 9
const RAIL_WIDTH = 64
const textEl = ref<HTMLElement>()
const knots = ref<{ cluster: string, y: number }[]>([])
const railHeight = ref(0)

function measure() {
  const el = textEl.value
  if (!el) return
  railHeight.value = el.offsetHeight
  knots.value = [...el.querySelectorAll<HTMLElement>('[data-cluster]')].map(s => ({
    cluster: s.dataset.cluster!,
    // Offsets are relative to the reader column, which the rail shares; an inline span wrapped
    // across lines reports its first line's top, so centre on one line height.
    y: s.offsetTop + Math.min(s.offsetHeight, 33) / 2
  }))
}

const threads = computed(() => events.value.clusters.slice(0, MAX_LANES).map((c, lane) => {
  const ys = knots.value.filter(k => k.cluster === c.id).map(k => k.y)
  return { ...c, x: RAIL_WIDTH - 12 - lane * LANE_GAP, ys, top: Math.min(...ys), bottom: Math.max(...ys) }
}).filter(t => t.ys.length))

let observer: ResizeObserver | undefined
watch(textEl, (el) => {
  observer?.disconnect()
  if (!el) return
  nextTick(measure)
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => measure())
    observer.observe(el)
  }
  document.fonts?.ready.then(measure).catch(() => {})
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div>
    <Crumbs class="mb-5" :items="[{ label: 'Runs', to: '/runs' }, { label: 'This run', to: `/runs/${id}` }, { label: 'Clusters', to: `/runs/${id}/clusters` }]" />
    <p v-if="loading" class="text-muted">
      Loading document...
    </p>
    <UAlert v-else-if="loadError || !doc" color="error" variant="subtle" title="Could not load the document" :description="loadError" />
    <DocumentReader
      v-else
      :title="doc.title"
      :outlet="doc.outlet"
      :byline="doc.byline"
      :published-at="doc.published_at"
      :url="doc.url"
      :language="doc.language"
      :words-from="doc.text"
    >
      <template #rail>
        <svg :width="RAIL_WIDTH" :height="railHeight" class="overflow-visible" aria-hidden="true">
          <g
            v-for="t in threads"
            :key="t.id"
            class="cursor-pointer transition-opacity duration-150"
            :style="{ opacity: focused && focused !== t.id ? 0.25 : 1 }"
            @pointerenter="hovered = t.id"
            @pointerleave="hovered = null"
          >
            <line :x1="t.x" :x2="t.x" :y1="t.top" :y2="t.bottom" :stroke="t.colour" :stroke-width="focused === t.id ? 3 : 2" stroke-linecap="round" />
            <g v-for="y in t.ys" :key="y">
              <line :x1="t.x" :x2="RAIL_WIDTH + 8" :y1="y" :y2="y" :stroke="t.colour" stroke-width="1" stroke-dasharray="1 3" />
              <circle :cx="t.x" :cy="y" r="4" :fill="t.colour" stroke="var(--ui-bg)" stroke-width="2" />
            </g>
            <!-- A wide invisible hit area so the thread is easy to hover. -->
            <rect :x="t.x - 4" :y="t.top - 6" width="8" :height="t.bottom - t.top + 12" fill="transparent" />
          </g>
        </svg>
      </template>

      <template #body>
        <p ref="textEl" data-test="text" class="whitespace-pre-wrap break-words"><template v-for="(p, i) in pieces" :key="i"><UPopover
          v-if="p.mention"
          mode="hover"
          :open-delay="0"
          :close-delay="150"
        ><span
          data-test="mention"
          :data-cluster="p.mention.cluster_id ?? undefined"
          class="cursor-help"
          :class="mentionClass(p.mention)"
          :style="fill(p.mention) ? { '--thread': fill(p.mention) } : undefined"
          @pointerenter="hovered = p.mention.cluster_id"
          @pointerleave="hovered = null"
        >{{ p.text }}</span><template #content><div class="w-72 space-y-3 p-4 font-sans text-sm" data-test="popover">
          <template v-if="p.mention.cluster_id">
            <dl v-if="cardOf(p.mention.cluster_id)" class="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5">
              <dt class="text-muted">Action</dt><dd class="font-semibold text-highlighted">{{ cardOf(p.mention.cluster_id)?.action || '—' }}</dd>
              <dt class="text-muted">Participants</dt><dd>{{ cardOf(p.mention.cluster_id)?.participants || '—' }}</dd>
              <dt class="text-muted">Location</dt><dd>{{ cardOf(p.mention.cluster_id)?.location || '—' }}</dd>
              <dt class="text-muted">Time</dt><dd>{{ cardOf(p.mention.cluster_id)?.time || '—' }}</dd>
            </dl>
            <p v-else class="text-muted">No card for this cluster.</p>
            <NuxtLink :to="`/runs/${id}/clusters/${p.mention.cluster_id}`" class="block border-t border-default pt-2.5 font-semibold text-primary hover:underline">
              Cluster of {{ p.mention.cluster_size }} mentions
            </NuxtLink>
          </template>
          <p v-else class="text-muted">Not clustered.</p>
        </div></template></UPopover><template v-else>{{ p.text }}</template></template></p>
      </template>

      <template #aside>
        <section aria-labelledby="events-heading" class="rounded-xl border border-default bg-default p-5">
          <h2 id="events-heading" class="text-sm font-bold text-highlighted">
            Events in this document
          </h2>
          <p v-if="!events.clusters.length && !events.unclustered" class="mt-2 text-sm text-muted">
            No event mentions were found in this document.
          </p>
          <ul class="mt-3 space-y-1">
            <li v-for="e in events.clusters" :key="e.id" class="group rounded-md" :class="focused === e.id ? 'bg-elevated' : ''">
              <button
                type="button"
                class="flex w-full items-start gap-3 rounded-md px-2 py-2 text-left"
                :aria-pressed="pinned === e.id"
                @click="pinned = pinned === e.id ? null : e.id"
                @pointerenter="hovered = e.id"
                @pointerleave="hovered = null"
                @focus="hovered = e.id"
                @blur="hovered = null"
              >
                <span aria-hidden="true" class="mt-1 h-4 w-1 shrink-0 rounded-full" :style="{ background: e.colour }" />
                <span class="min-w-0">
                  <span class="block break-words text-sm font-semibold text-highlighted">{{ cardOf(e.id)?.action || 'Untitled event' }}</span>
                  <span class="block text-xs text-muted">
                    <span class="tabular">{{ e.here }}</span> here<template v-if="e.size !== null">, <span class="tabular">{{ e.size }}</span> across the run</template>
                  </span>
                </span>
              </button>
              <NuxtLink :to="`/runs/${id}/clusters/${e.id}`" class="ml-6 block px-2 pb-2 text-xs font-semibold text-primary hover:underline">
                Open cluster
              </NuxtLink>
            </li>
          </ul>
          <p v-if="events.unclustered" class="mt-3 flex items-center gap-3 border-t border-default px-2 pt-3 text-xs text-muted">
            <span aria-hidden="true" class="w-4 border-b-2 border-dashed border-muted" />
            {{ events.unclustered }} {{ events.unclustered === 1 ? 'mention' : 'mentions' }} not clustered
          </p>
          <p v-if="events.clusters.length" class="mt-4 text-xs leading-relaxed text-dimmed">
            Each colour is one event. Select an event to trace its mentions through the text.
          </p>
        </section>
      </template>
    </DocumentReader>
  </div>
</template>
