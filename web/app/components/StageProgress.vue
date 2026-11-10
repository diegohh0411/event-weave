<script setup lang="ts">
import type { components } from '../api/schema'

type Progress = components['schemas']['Progress']

const props = defineProps<{ progress: Record<string, Progress> }>()

const ORDER = ['provide', 'extract', 'tag', 'enrich', 'embed', 'cluster']
const rows = computed(() => Object.entries(props.progress)
  .sort(([a], [b]) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99))
  .map(([stage, p]) => {
    const total = p.queued + p.running + p.done + p.failed
    return { stage, p, total, percent: total ? Math.round((p.done / total) * 100) : 0 }
  }))
</script>

<template>
  <p v-if="!rows.length" class="text-muted">
    No jobs yet.
  </p>
  <ul v-else class="space-y-5">
    <li v-for="r in rows" :key="r.stage" class="space-y-2">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
        <span class="font-semibold text-highlighted">{{ r.stage }} <span class="tabular ml-1 font-normal text-muted">{{ r.percent }}%</span></span>
        <span class="tabular flex flex-wrap gap-x-3 text-muted">
          <span>{{ r.p.queued }} queued</span>
          <span>{{ r.p.running }} running</span>
          <span>{{ r.p.done }} done</span>
          <span :class="r.p.failed ? 'font-semibold text-warning' : ''">{{ r.p.failed }} failed</span>
        </span>
      </div>
      <UProgress :model-value="r.percent" :max="100" size="sm" :color="r.p.failed ? 'warning' : 'primary'" />
    </li>
  </ul>
</template>
