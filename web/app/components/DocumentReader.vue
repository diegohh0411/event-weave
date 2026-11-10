<script setup lang="ts">
const props = defineProps<{
  title: string
  outlet?: string | null
  byline?: string | null
  publishedAt?: string | null
  url?: string | null
  language?: string | null
  /** Plain text to show when no `body` slot is given. */
  text?: string
  /** Word count source for the reading-time estimate. */
  wordsFrom?: string
}>()

const date = computed(() => (props.publishedAt ? props.publishedAt.slice(0, 10) : null))
const minutes = computed(() => {
  const words = (props.wordsFrom ?? props.text ?? '').trim().split(/\s+/).filter(Boolean).length
  return words ? Math.max(1, Math.round(words / 230)) : null
})
</script>

<template>
  <article :class="$slots.aside ? 'lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-10' : ''">
    <div class="min-w-0 rounded-xl border border-default bg-default px-5 py-7 sm:px-10 sm:py-10" :class="$slots.rail ? 'lg:pl-24' : ''">
      <header class="mx-auto max-w-[66ch] space-y-4 border-b border-default pb-7">
        <p class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
          <span class="font-semibold text-highlighted">{{ outlet || 'Unknown outlet' }}</span>
          <time v-if="date" :datetime="publishedAt ?? undefined" class="tabular">{{ date }}</time>
          <span v-else>Date unknown</span>
          <UBadge v-if="language" color="neutral" variant="outline" size="sm">{{ language }}</UBadge>
          <span v-if="minutes">{{ minutes }} min read</span>
        </p>
        <h1 :lang="language ?? undefined" class="font-serif text-[2rem] font-semibold leading-[1.15] tracking-[-0.01em] text-highlighted sm:text-[2.5rem]" style="font-optical-sizing: auto">
          {{ title }}
        </h1>
        <p v-if="byline" class="text-[0.9375rem] text-muted">
          By {{ byline }}
        </p>
        <a v-if="url" :href="url" target="_blank" rel="noopener noreferrer" class="block truncate text-sm text-primary underline decoration-[var(--ui-border-accented)] underline-offset-4 hover:decoration-current">{{ url }}</a>
      </header>

      <div class="relative mx-auto mt-8 max-w-[66ch]">
        <div v-if="$slots.rail" class="absolute inset-y-0 -left-20 hidden w-16 lg:block">
          <slot name="rail" />
        </div>
        <div :lang="language ?? undefined" class="font-serif text-[1.1875rem] leading-[1.75] text-highlighted" style="font-optical-sizing: auto">
          <slot name="body">
            <p class="whitespace-pre-wrap break-words">{{ text }}</p>
          </slot>
        </div>
      </div>
    </div>

    <aside v-if="$slots.aside" class="mt-6 lg:mt-0">
      <div class="lg:sticky lg:top-8">
        <slot name="aside" />
      </div>
    </aside>
  </article>
</template>
