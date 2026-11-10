<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  crumbs?: { label: string, to: string }[]
}>()
</script>

<template>
  <header class="mb-6 space-y-3">
    <Crumbs v-if="crumbs?.length" :items="crumbs" />
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div class="min-w-0 max-w-3xl">
        <h1 class="break-words text-[1.75rem] font-bold leading-tight tracking-[-0.02em] text-highlighted">
          {{ title }}
        </h1>
        <p v-if="description" class="mt-1.5 text-[0.9375rem] text-muted">
          {{ description }}
        </p>
        <slot name="meta" />
      </div>
      <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
        <slot name="actions" />
      </div>
    </div>
  </header>
</template>
