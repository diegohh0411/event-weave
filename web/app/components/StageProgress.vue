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
  <ul v-else class="space-y-4">
    <li v-for="r in rows" :key="r.stage" class="space-y-1">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 text-sm">
        <span class="font-medium">{{ r.stage }}</span>
        <span class="text-muted">{{ r.p.queued }} queued · {{ r.p.running }} running · {{ r.p.done }} done · {{ r.p.failed }} failed</span>
      </div>
      <UProgress :model-value="r.percent" :max="100" :color="r.p.failed ? 'warning' : 'primary'" />
    </li>
  </ul>
</template>
