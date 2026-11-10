<script setup lang="ts">
const props = defineProps<{ runId: string }>()
const route = useRoute()

const tabs = computed(() => {
  const base = `/runs/${props.runId}`
  return [
    { label: 'Overview', to: base, active: route.path === base },
    { label: 'Clusters', to: `${base}/clusters`, active: route.path.startsWith(`${base}/clusters`) },
    { label: 'Spot check', to: `${base}/spot-check`, active: route.path.startsWith(`${base}/spot-check`) },
    { label: 'Scores', to: `/compare?run_id=${props.runId}`, active: false }
  ]
})
</script>

<template>
  <nav aria-label="Run sections" class="-mt-2 mb-6 overflow-x-auto border-b border-default">
    <ul class="flex min-w-max gap-1">
      <li v-for="t in tabs" :key="t.label">
        <NuxtLink
          :to="t.to"
          :aria-current="t.active ? 'page' : undefined"
          class="relative block px-3 pb-2.5 pt-1 text-sm font-semibold transition-colors"
          :class="t.active ? 'text-highlighted after:absolute after:inset-x-3 after:-bottom-px after:h-0.5 after:rounded-full after:bg-[var(--ew-thread)]' : 'text-muted hover:text-highlighted'"
        >{{ t.label }}</NuxtLink>
      </li>
    </ul>
  </nav>
</template>
