<script setup lang="ts">
defineProps<{ items: { label: string, to: string }[] }>()
</script>

<template>
  <nav aria-label="Breadcrumb">
    <ol class="flex flex-wrap items-center gap-1.5 text-sm text-muted">
      <li v-for="(c, i) in items" :key="c.to" class="flex items-center gap-1.5">
        <span v-if="i" aria-hidden="true" class="text-dimmed">/</span>
        <NuxtLink :to="c.to" class="rounded-sm hover:text-highlighted hover:underline">
          {{ c.label }}
        </NuxtLink>
      </li>
    </ol>
  </nav>
</template>
