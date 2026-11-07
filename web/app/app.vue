<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const route = useRoute()
const links = computed<NavigationMenuItem[]>(() => [
  { label: 'Builder', to: '/', active: route.path === '/' },
  { label: 'Runs', to: '/runs', active: route.path.startsWith('/runs') },
  { label: 'Compare', to: '/compare', active: route.path.startsWith('/compare') }
])
</script>

<template>
  <UApp>
    <UHeader title="event-weave">
      <UNavigationMenu :items="links" />
    </UHeader>
    <UMain>
      <UContainer class="py-6">
        <NuxtPage />
      </UContainer>
    </UMain>
  </UApp>
</template>
