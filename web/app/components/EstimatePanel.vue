<script setup lang="ts">
import type { components } from '../api/schema'

const props = defineProps<{ estimate: components['schemas']['EstimateOut'] | null }>()
const stages = computed(() => Object.entries(props.estimate?.cached ?? {}))
</script>

<template>
  <section class="rounded-xl border border-default bg-default p-5">
    <h2 class="text-sm font-bold text-highlighted">
      Estimate
    </h2>
    <p v-if="!estimate" class="mt-2 text-sm text-muted">
      Press Validate to estimate this run.
    </p>
    <template v-else>
      <p v-if="estimate.amount === null" class="mt-2 text-sm">
        No estimate yet: {{ estimate.reason ?? 'unknown reason' }}
      </p>
      <p v-else class="mt-1 text-3xl font-bold tracking-tight text-highlighted">
        <span class="tabular">${{ estimate.amount }}</span> <span class="text-sm font-normal text-muted">estimated</span>
      </p>
      <div v-if="stages.length" class="mt-4 border-t border-default pt-3">
        <p class="text-xs text-muted">
          Units already cached / total, per stage. The amount still includes cache hits.
        </p>
        <ul class="mt-2 space-y-1 text-sm">
          <li v-for="[stage, c] in stages" :key="stage" class="flex justify-between gap-4">
            <span>{{ stage }}</span>
            <span class="tabular text-highlighted">{{ c.cached }} / {{ c.total }}</span>
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>
