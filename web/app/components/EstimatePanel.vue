<script setup lang="ts">
import type { components } from '../api/schema'

const props = defineProps<{ estimate: components['schemas']['EstimateOut'] | null }>()
const stages = computed(() => Object.entries(props.estimate?.cached ?? {}))
</script>

<template>
  <UCard>
    <template #header>
      <h2 class="font-semibold">
        Estimate
      </h2>
    </template>
    <p v-if="!estimate" class="text-sm text-muted">
      Press Validate to estimate this run.
    </p>
    <template v-else>
      <p v-if="estimate.amount === null" class="text-sm">
        No estimate yet: {{ estimate.reason ?? 'unknown reason' }}
      </p>
      <p v-else class="text-lg font-semibold">
        ${{ estimate.amount }} <span class="text-xs font-normal text-muted">estimated</span>
      </p>
      <div v-if="stages.length" class="mt-3">
        <p class="text-xs text-muted">
          Units already cached / total, per stage. The amount still includes cache hits.
        </p>
        <ul class="mt-1 text-sm">
          <li v-for="[stage, c] in stages" :key="stage" class="flex justify-between gap-4">
            <span>{{ stage }}</span>
            <span class="tabular-nums">{{ c.cached }} / {{ c.total }}</span>
          </li>
        </ul>
      </div>
    </template>
  </UCard>
</template>
