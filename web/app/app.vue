<script setup lang="ts">
const route = useRoute()

// In the order the work happens: collect articles, configure a run, watch it, compare results.
const links = computed(() => [
  { label: 'Crawls', to: '/crawls', active: route.path.startsWith('/crawls') },
  { label: 'New run', to: '/', active: route.path === '/' },
  { label: 'Runs', to: '/runs', active: route.path.startsWith('/runs') },
  { label: 'Compare', to: '/compare', active: route.path.startsWith('/compare') }
])

const menuOpen = ref(false)
watch(() => route.fullPath, () => { menuOpen.value = false })
</script>

<template>
  <UApp>
    <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <header class="sticky top-0 z-30 border-b border-default bg-[var(--ew-loom)]/90 backdrop-blur lg:static lg:h-dvh lg:border-b-0 lg:border-r lg:bg-transparent lg:backdrop-blur-none">
        <div class="lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col">
          <div class="flex items-center justify-between gap-3 px-4 py-3 lg:px-5 lg:pb-6 lg:pt-6">
            <NuxtLink to="/crawls" class="flex items-center gap-2.5 rounded-md text-highlighted" aria-label="Event Weave home">
              <BrandMark class="size-7" />
              <span class="text-[1.0625rem] font-bold tracking-[-0.01em]">Event Weave</span>
            </NuxtLink>
            <UButton
              class="lg:hidden"
              color="neutral"
              variant="ghost"
              size="sm"
              :aria-expanded="menuOpen"
              aria-controls="primary-nav"
              @click="menuOpen = !menuOpen"
            >
              {{ menuOpen ? 'Close' : 'Menu' }}
            </UButton>
          </div>

          <nav id="primary-nav" aria-label="Primary" :class="menuOpen ? 'block' : 'hidden lg:block'" class="px-3 pb-4 lg:flex-1 lg:px-4">
            <ol class="relative space-y-0.5">
              <!-- The thread every section hangs on; the active section is knotted to it. -->
              <span aria-hidden="true" class="absolute bottom-4 left-[0.9375rem] top-4 w-px bg-[var(--ew-thread)] opacity-30" />
              <li v-for="l in links" :key="l.to">
                <NuxtLink
                  :to="l.to"
                  :aria-current="l.active ? 'page' : undefined"
                  class="group relative flex items-center gap-3 rounded-md py-2 pl-8 pr-3 transition-colors"
                  :class="l.active ? 'bg-default text-highlighted shadow-[0_1px_0_var(--ui-border)]' : 'text-muted hover:bg-default/60 hover:text-highlighted'"
                >
                  <span
                    aria-hidden="true"
                    class="absolute left-[0.6875rem] top-1/2 size-2 -translate-y-1/2 rounded-full border-2 border-[var(--ew-thread)]"
                    :class="l.active ? 'bg-[var(--ew-thread)]' : 'bg-[var(--ew-loom)]'"
                  /><span class="text-sm font-semibold">{{ l.label }}</span>
                </NuxtLink>
              </li>
            </ol>
          </nav>

          <p class="hidden px-5 pb-5 text-xs leading-relaxed text-dimmed lg:block">
            Finds event mentions in the news and weaves the ones about the same event together, across outlets and languages.
          </p>
        </div>
      </header>

      <main class="min-w-0 px-4 pb-16 pt-6 sm:px-6 lg:px-10 lg:pt-8">
        <div class="mx-auto max-w-6xl">
          <NuxtPage />
        </div>
      </main>
    </div>
  </UApp>
</template>
