<script setup lang="ts">
import { highlightSegments } from '../utils/highlight'

const props = defineProps<{ text: string, ranges: readonly (readonly [number, number])[] }>()
const segments = computed(() => highlightSegments(props.text, props.ranges))
</script>

<template>
  <span class="break-words"><template v-for="(s, i) in segments" :key="i"><mark v-if="s.mark" class="rounded-sm bg-[var(--ew-weld)] px-0.5 font-semibold text-highlighted shadow-[inset_0_-2px_0_var(--color-weld-500)]">{{ s.text }}</mark><template v-else>{{ s.text }}</template></template></span>
</template>
