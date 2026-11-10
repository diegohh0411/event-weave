<script setup lang="ts">
import { useApi } from '../../../api/client'
import { apiError } from '../../../utils/apiError'
const route = useRoute(); const api = useApi()
const { data: site, error } = await useAsyncData(`site-edit-${route.params.id}`, async () => { const r = await api.GET('/sites/{site_id}', { params: { path: { site_id: String(route.params.id) } } }); if (!r.data) throw new Error(apiError(r.error)); return r.data })
</script>
<template><div><Crumbs class="mb-4" :items="[{ label: 'Sites', to: '/sites' }]" /><PageHeader title="Edit site" /><UAlert v-if="error" color="error" :description="error.message" /><SiteForm v-else-if="site" :site="site" /></div></template>
