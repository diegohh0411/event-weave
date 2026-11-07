<script setup lang="ts">
import type { FieldSpec } from '../utils/schemaForm'

const props = defineProps<{ fields: FieldSpec[], modelValue: Record<string, unknown> }>()
const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  'update:invalid': [invalid: boolean]
}>()

// Text of json fields as typed, and which of them do not parse.
const drafts = reactive<Record<string, string>>({})
const bad = reactive<Record<string, boolean>>({})

// A form going away takes its invalid drafts with it.
onBeforeUnmount(() => emit('update:invalid', false))

const shown = (f: FieldSpec) => (f.key in props.modelValue ? props.modelValue[f.key] : f.default)

function set(f: FieldSpec, value: unknown) {
  const next = { ...props.modelValue }
  if (value === undefined) delete next[f.key]
  else next[f.key] = value
  emit('update:modelValue', next)
}

function setNumber(f: FieldSpec, raw: string | number) {
  if (raw === '' || raw === null || raw === undefined) return set(f, f.nullable ? null : undefined)
  const n = Number(raw)
  set(f, Number.isNaN(n) ? undefined : (f.kind === 'int' ? Math.trunc(n) : n))
}

function setText(f: FieldSpec, raw: string) {
  set(f, raw === '' ? (f.nullable ? null : undefined) : raw)
}

const toOption = (f: FieldSpec, s: string) => (f.numeric ? Number(s) : s)

function setJson(f: FieldSpec, text: string) {
  drafts[f.key] = text
  try {
    const parsed = text.trim() === '' ? undefined : JSON.parse(text)
    bad[f.key] = false
    set(f, parsed)
  } catch {
    bad[f.key] = true
  }
  emit('update:invalid', Object.values(bad).some(Boolean))
}

const jsonText = (f: FieldSpec) => drafts[f.key] ?? (shown(f) === undefined ? '' : JSON.stringify(shown(f), null, 2))
// <input type=datetime-local> wants "YYYY-MM-DDTHH:mm"
const localTime = (v: unknown) => (typeof v === 'string' ? v.slice(0, 16) : '')
</script>

<template>
  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
    <UFormField
      v-for="f in fields"
      :key="f.key"
      :label="f.label"
      :class="f.kind === 'json' ? 'sm:col-span-2' : ''"
      :error="bad[f.key] ? 'Must be valid JSON' : undefined"
    >
      <USelect
        v-if="f.kind === 'select'"
        class="w-full"
        :items="[...(f.nullable ? [{ label: 'None', value: '__none__' }] : []), ...(f.options ?? []).map(o => ({ label: o, value: o }))]"
        :model-value="shown(f) == null ? (f.nullable ? '__none__' : undefined) : String(shown(f))"
        @update:model-value="(v: string) => set(f, v === '__none__' ? null : toOption(f, v))"
      />
      <USelect
        v-else-if="f.kind === 'multiselect'"
        class="w-full"
        multiple
        :items="(f.options ?? []).map(o => ({ label: o, value: o }))"
        :model-value="((shown(f) as unknown[] | undefined) ?? []).map(String)"
        @update:model-value="(v: string[]) => set(f, v.map(o => toOption(f, o)))"
      />
      <USwitch
        v-else-if="f.kind === 'bool'"
        :model-value="Boolean(shown(f))"
        @update:model-value="(v: boolean) => set(f, v)"
      />
      <UInput
        v-else-if="f.kind === 'int' || f.kind === 'number'"
        class="w-full"
        type="number"
        :step="f.kind === 'int' ? 1 : 'any'"
        :model-value="(shown(f) as number | null | undefined) ?? undefined"
        @update:model-value="(v: string | number) => setNumber(f, v)"
      />
      <UInput
        v-else-if="f.kind === 'datetime'"
        class="w-full"
        type="datetime-local"
        :model-value="localTime(shown(f))"
        @update:model-value="(v: string) => setText(f, v)"
      />
      <UTextarea
        v-else-if="f.kind === 'json'"
        class="w-full font-mono"
        :rows="4"
        :model-value="jsonText(f)"
        @update:model-value="(v: string) => setJson(f, v)"
      />
      <UInput
        v-else
        class="w-full"
        :model-value="(shown(f) as string | null | undefined) ?? ''"
        @update:model-value="(v: string) => setText(f, v)"
      />
    </UFormField>
  </div>
</template>
