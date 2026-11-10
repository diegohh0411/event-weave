<script setup lang="ts">
const route = useRoute()
const { runs, crawls, collapsed, setCollapsed, refresh } = useWorkspace()

const mobileOpen = ref(false)
const searchOpen = ref(false)

// Keep the recents fresh as you move around; a new run or crawl shows up as soon as you open it.
onMounted(() => refresh())
watch(() => route.path, () => {
  mobileOpen.value = false
  void refresh()
})

defineShortcuts({
  meta_k: () => { searchOpen.value = !searchOpen.value },
  meta_b: () => setCollapsed(!collapsed.value)
})

const groups = computed(() => [
  {
    id: 'pages',
    label: 'Go to',
    items: [
      { label: 'New run', icon: 'i-lucide-plus', to: '/' },
      { label: 'Crawls', icon: 'i-lucide-radar', to: '/crawls' },
      { label: 'Runs', icon: 'i-lucide-workflow', to: '/runs' },
      { label: 'Compare', icon: 'i-lucide-chart-column', to: '/compare' }
    ]
  },
  { id: 'runs', label: 'Runs', items: runs.value.map(r => ({ label: r.label, suffix: r.status, icon: 'i-lucide-workflow', to: r.to })) },
  { id: 'crawls', label: 'Crawls', items: crawls.value.map(c => ({ label: c.label, suffix: c.status, icon: 'i-lucide-radar', to: c.to })) }
].filter(g => g.items.length))

function go(item: { to?: unknown }) {
  if (typeof item.to !== 'string') return
  searchOpen.value = false
  void navigateTo(item.to)
}
watch(searchOpen, (open) => { if (open) void refresh(30) })
</script>

<template>
  <UApp>
    <div class="flex h-dvh overflow-hidden">
      <aside
        class="hidden shrink-0 border-r border-[var(--ew-sidebar-border)] bg-[var(--ew-sidebar)] transition-[width] duration-200 lg:block"
        :class="collapsed ? 'w-14' : 'w-64'"
      >
        <AppSidebar :collapsed="collapsed" collapsible @search="searchOpen = true" />
      </aside>

      <div class="flex min-w-0 flex-1 flex-col">
        <header class="flex h-12 shrink-0 items-center gap-2 border-b border-default bg-[var(--ew-sidebar)] px-3 lg:hidden">
          <UButton color="neutral" variant="ghost" size="sm" square icon="i-lucide-menu" aria-label="Open navigation" @click="mobileOpen = true" />
          <NuxtLink to="/runs" class="flex items-center gap-2 text-highlighted">
            <BrandMark class="size-5" />
            <span class="text-sm font-bold">Event Weave</span>
          </NuxtLink>
          <UButton class="ml-auto" color="neutral" variant="ghost" size="sm" square icon="i-lucide-search" aria-label="Search" @click="searchOpen = true" />
        </header>

        <main class="min-h-0 flex-1 overflow-y-auto">
          <div class="mx-auto max-w-6xl px-4 pb-16 pt-6 sm:px-6 lg:px-10 lg:pt-8">
            <NuxtPage />
          </div>
        </main>
      </div>
    </div>

    <USlideover v-model:open="mobileOpen" side="left" title="Navigation" :ui="{ content: 'max-w-72 bg-[var(--ew-sidebar)]', header: 'sr-only', body: 'p-0 sm:p-0' }">
      <template #body>
        <AppSidebar @search="mobileOpen = false; searchOpen = true" />
      </template>
    </USlideover>

    <UModal v-model:open="searchOpen" title="Search" description="Jump to a page, run or crawl" :ui="{ content: 'max-w-xl', header: 'sr-only' }">
      <template #content>
        <UCommandPalette
          :groups="groups"
          placeholder="Search runs, crawls and pages"
          class="h-96"
          close
          @update:model-value="go"
          @update:open="searchOpen = $event"
        />
      </template>
    </UModal>
  </UApp>
</template>
