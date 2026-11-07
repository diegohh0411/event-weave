<script setup lang="ts">
import { readyDeploymentsFor, type Capability, type DeploymentOut } from '../utils/runConfig'

const props = defineProps<{ capability: Capability, deployments: DeploymentOut[], modelValue: string | undefined }>()
const emit = defineEmits<{ 'update:modelValue': [id: string | undefined] }>()

const ready = computed(() => readyDeploymentsFor(props.deployments, props.capability))
const items = computed(() => ready.value.map(d => ({ label: d.local ? `${d.id} (local)` : d.id, value: d.id })))
const notReady = computed(() => props.deployments.filter(d => !d.ready && d.capability === props.capability))

// Keep the choice valid: default to the default deployment (or the first), drop one that went away.
watchEffect(() => {
  const ids = ready.value.map(d => d.id)
  if (props.modelValue && ids.includes(props.modelValue)) return
  const pick = ready.value.find(d => d.is_default) ?? ready.value[0]
  emit('update:modelValue', pick?.id)
})
</script>

<template>
  <UFormField :label="`Deployment: ${capability}`">
    <UAlert
      v-if="!ready.length"
      color="error"
      variant="subtle"
      :title="`No ready deployment for ${capability}`"
    />
    <USelect
      v-else
      class="w-full"
      :items="items"
      :model-value="modelValue"
      @update:model-value="emit('update:modelValue', $event as string)"
    />
    <ul v-if="notReady.length" class="mt-1 text-xs text-muted">
      <li v-for="d in notReady" :key="d.id">
        {{ d.id }} is not ready<template v-if="d.reason">: {{ d.reason }}</template>
      </li>
    </ul>
  </UFormField>
</template>
