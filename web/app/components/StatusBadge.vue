<script setup lang="ts">
const props = defineProps<{ status: string }>()

type Colour = 'neutral' | 'primary' | 'success' | 'warning' | 'error' | 'info'
const COLOURS: Record<string, Colour> = {
  created: 'neutral', queued: 'neutral', pending: 'neutral',
  running: 'info',
  updated: 'info', unchanged: 'neutral', removed: 'warning', completed: 'success', done: 'success', stored: 'success', existing: 'neutral',
  cancelled: 'warning', duplicate: 'neutral', rejected: 'warning', skipped: 'warning',
  failed: 'error', error: 'error'
}
const colour = computed<Colour>(() => COLOURS[props.status] ?? 'neutral')
</script>

<template>
  <UBadge :color="colour" variant="soft" size="sm" class="gap-1.5 font-semibold">
    <span v-if="status === 'running'" aria-hidden="true" class="size-1.5 rounded-full bg-current motion-safe:animate-pulse" />{{ status }}
  </UBadge>
</template>
