<script setup lang="ts">
import type { components } from '../api/schema'

type Decision = components['schemas']['Decision']

const props = defineProps<{ decision: Decision | null, runId: string, clusterId: string }>()

const fmt = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(Math.round(n * 1000) / 1000))
const rows = computed(() => (props.decision?.candidates ?? []).map(c => ({
  ...c,
  probability: c.label && props.decision?.probabilities ? props.decision.probabilities[c.label] : undefined,
  chosen: c.cluster_id === props.clusterId && props.decision?.outcome === 'join'
})))
</script>

<template>
  <div data-test="decision" class="space-y-2 text-sm">
    <p v-if="!decision" class="text-muted">
      No decision recorded
    </p>
    <template v-else>
      <p>
        <span class="font-medium">{{ decision.outcome ?? 'no outcome' }}</span>
        <span v-if="decision.adapter" class="text-muted"> · {{ decision.adapter }}<template v-if="decision.deployment"> ({{ decision.deployment }})</template></span>
      </p>
      <p v-if="decision.reason" class="break-words">
        {{ decision.reason }}
      </p>
      <p class="text-muted">
        Noul: {{ fmt(decision.noul) }}
      </p>
      <p v-if="!rows.length" class="text-muted">
        No candidates
      </p>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-left">
          <thead class="text-muted">
            <tr>
              <th class="pr-3 font-normal">
                Label
              </th>
              <th class="pr-3 font-normal">
                Cluster
              </th>
              <th class="pr-3 font-normal">
                Retrieval
              </th>
              <th class="pr-3 font-normal">
                Fields
              </th>
              <th class="font-normal">
                Probability
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in rows" :key="c.cluster_id" :class="c.chosen ? 'bg-primary/10' : ''">
              <td class="pr-3">
                {{ c.label ?? '—' }}
                <span v-if="c.chosen" data-test="chosen" class="ml-1 text-xs font-semibold text-primary">chosen</span>
              </td>
              <td class="pr-3">
                <NuxtLink :to="`/runs/${runId}/clusters/${c.cluster_id}`" class="text-primary hover:underline">
                  {{ c.cluster_id.slice(0, 8) }}
                </NuxtLink>
              </td>
              <td class="pr-3">
                {{ fmt(c.score) }}
              </td>
              <td class="pr-3">
                <template v-if="c.field_scores">
                  <span v-for="(v, k) in c.field_scores" :key="k" class="mr-2 whitespace-nowrap">{{ k }} {{ fmt(v) }}</span>
                </template>
                <template v-else>
                  —
                </template>
              </td>
              <td>{{ fmt(c.probability) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
