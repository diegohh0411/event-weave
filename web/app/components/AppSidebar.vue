<script setup lang="ts">
const props = defineProps<{ collapsed?: boolean, collapsible?: boolean }>()
const emit = defineEmits<{ search: [] }>()

const route = useRoute()
const { runs, crawls, apiOnline, setCollapsed } = useWorkspace()
const colorMode = useColorMode()
const apiBase = useRuntimeConfig().public.apiBase

const isActive = (to: string) => route.path === to || route.path.startsWith(`${to}/`)
// A section is highlighted only when no recent item below already marks where you are.
const inRecents = computed(() => !props.collapsed && [...runs.value, ...crawls.value].some(r => isActive(r.to)))
const nav = computed(() => [
  { label: 'Crawls', icon: 'i-lucide-radar', to: '/crawls' },
  { label: 'Runs', icon: 'i-lucide-workflow', to: '/runs' },
  { label: 'Compare', icon: 'i-lucide-chart-column', to: '/compare' }
].map(n => ({ ...n, active: route.path.startsWith(n.to) && !inRecents.value })))

const DOT: Record<string, string> = {
  running: 'bg-[var(--ui-info)] motion-safe:animate-pulse',
  done: 'bg-[var(--ui-success)]', completed: 'bg-[var(--ui-success)]',
  cancelled: 'bg-[var(--ui-warning)]', failed: 'bg-[var(--ui-error)]'
}
const IDLE_DOT = 'bg-[var(--ui-text-dimmed)]'

const THEMES = ['system', 'light', 'dark'] as const
const themeIcon = computed(() => ({ system: 'i-lucide-monitor', light: 'i-lucide-sun', dark: 'i-lucide-moon' })[colorMode.preference as 'system'] ?? 'i-lucide-monitor')
function cycleTheme() {
  const i = THEMES.indexOf(colorMode.preference as typeof THEMES[number])
  colorMode.preference = THEMES[(i + 1) % THEMES.length]!
}

const host = computed(() => apiBase.replace(/^https?:\/\//, ''))
const isMac = import.meta.client && /Mac|iPhone|iPad/.test(navigator.userAgent)

const item = 'flex h-9 items-center gap-3 rounded-lg px-2.5 text-sm transition-colors'
const idle = 'text-default hover:bg-[var(--ew-sidebar-hover)] hover:text-highlighted'
const on = 'bg-[var(--ew-sidebar-active)] font-semibold text-highlighted'
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- Brand and collapse -->
    <div class="flex h-14 shrink-0 items-center gap-2 px-3" :class="props.collapsed ? 'justify-center' : 'justify-between'">
      <NuxtLink v-if="!props.collapsed" to="/runs" class="flex min-w-0 items-center gap-2 rounded-md px-1 text-highlighted">
        <BrandMark class="size-6" />
        <span class="truncate text-[0.9375rem] font-bold tracking-[-0.01em]">Event Weave</span>
      </NuxtLink>
      <UTooltip v-if="props.collapsible" :text="props.collapsed ? 'Expand sidebar' : 'Collapse sidebar'" :kbds="['meta', 'b']">
        <UButton
          color="neutral"
          variant="ghost"
          size="sm"
          square
          :icon="props.collapsed ? 'i-lucide-panel-left-open' : 'i-lucide-panel-left-close'"
          :aria-label="props.collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          @click="setCollapsed(!props.collapsed)"
        />
      </UTooltip>
    </div>

    <nav aria-label="Primary" class="shrink-0 space-y-0.5 px-2">
      <!-- The main action, then search, then the sections in the order the work happens. -->
      <UTooltip :text="props.collapsed ? 'New run' : undefined" :disabled="!props.collapsed" :content="{ side: 'right' }">
        <NuxtLink to="/" :aria-current="route.path === '/' ? 'page' : undefined" :class="[item, route.path === '/' ? on : idle, props.collapsed && 'justify-center px-0']">
          <span class="grid size-6 shrink-0 place-items-center rounded-full bg-primary text-inverted"><UIcon name="i-lucide-plus" class="size-4" /></span>
          <span v-if="!props.collapsed" class="font-semibold">New run</span>
        </NuxtLink>
      </UTooltip>
      <UTooltip :text="props.collapsed ? 'Search' : undefined" :disabled="!props.collapsed" :content="{ side: 'right' }">
        <button type="button" :class="[item, idle, 'w-full', props.collapsed && 'justify-center px-0']" @click="emit('search')">
          <UIcon name="i-lucide-search" class="size-[1.125rem] shrink-0 text-muted" />
          <template v-if="!props.collapsed">
            <span class="flex-1 text-left">Search</span>
            <span class="flex gap-0.5"><UKbd :value="isMac ? 'meta' : 'ctrl'" size="sm" /><UKbd value="K" size="sm" /></span>
          </template>
        </button>
      </UTooltip>
      <div class="pt-2" />
      <UTooltip v-for="n in nav" :key="n.to" :text="props.collapsed ? n.label : undefined" :disabled="!props.collapsed" :content="{ side: 'right' }">
        <NuxtLink :to="n.to" :aria-current="n.active ? 'page' : undefined" :class="[item, n.active ? on : idle, props.collapsed && 'justify-center px-0']">
          <UIcon :name="n.icon" class="size-[1.125rem] shrink-0" :class="n.active ? 'text-[var(--ew-thread)]' : 'text-muted'" />
          <span v-if="!props.collapsed">{{ n.label }}</span>
        </NuxtLink>
      </UTooltip>
    </nav>

    <!-- Recent work: quick jumps back into what you were looking at. -->
    <nav v-if="!props.collapsed" aria-label="Recent" class="mt-5 min-h-0 flex-1 space-y-5 overflow-y-auto px-2 pb-4">
      <section v-for="group in [{ title: 'Recent runs', items: runs, all: '/runs', none: 'No runs yet' }, { title: 'Recent crawls', items: crawls, all: '/crawls', none: 'No crawls yet' }]" :key="group.title">
        <h2 class="px-2.5 pb-1 text-xs font-semibold text-muted">
          {{ group.title }}
        </h2>
        <p v-if="!group.items.length" class="px-2.5 py-1 text-sm text-dimmed">
          {{ group.none }}
        </p>
        <ul v-else class="space-y-px">
          <li v-for="r in group.items" :key="r.id">
            <NuxtLink :to="r.to" :title="`${r.label} (${r.status})`" :class="[item, 'h-8', isActive(r.to) ? on : idle]">
              <span class="size-1.5 shrink-0 rounded-full" :class="DOT[r.status] ?? IDLE_DOT" />
              <span class="min-w-0 flex-1 truncate">{{ r.label }}</span>
              <span v-if="r.when" class="tabular shrink-0 text-xs text-dimmed">{{ r.when }}</span>
            </NuxtLink>
          </li>
        </ul>
      </section>
    </nav>
    <div v-else class="flex-1" />

    <!-- Connection and appearance -->
    <div class="flex shrink-0 items-center gap-2 border-t border-[var(--ew-sidebar-border)] p-2" :class="props.collapsed ? 'flex-col' : ''">
      <UTooltip :text="apiOnline === false ? `API unreachable at ${host}` : `API at ${host}`" :content="{ side: 'right' }">
        <div class="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-xs" :class="props.collapsed ? 'justify-center px-0' : ''">
          <span class="relative flex size-2 shrink-0">
            <span class="size-2 rounded-full" :class="apiOnline === null ? IDLE_DOT : apiOnline ? 'bg-[var(--ui-success)]' : 'bg-[var(--ui-error)]'" />
          </span>
          <span v-if="!props.collapsed" class="min-w-0">
            <span class="block font-semibold text-highlighted">{{ apiOnline === null ? 'Connecting' : apiOnline ? 'API connected' : 'API unreachable' }}</span>
            <span class="block truncate text-muted">{{ host }}</span>
          </span>
        </div>
      </UTooltip>
      <UTooltip :text="`Theme: ${colorMode.preference}`" :content="{ side: 'right' }">
        <UButton color="neutral" variant="ghost" size="sm" square :icon="themeIcon" :aria-label="`Theme: ${colorMode.preference}. Change theme`" @click="cycleTheme" />
      </UTooltip>
    </div>
  </div>
</template>
