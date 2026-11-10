<script setup lang="ts">
import { useApi } from '../../../../api/client'
import type { components } from '../../../../api/schema'
import { cardFields } from '../../../../utils/clusterCard'

type ClusterOut = components['schemas']['ClusterOut']
type Member = components['schemas']['Member']

const PER_PAGE = 20
const api = useApi()
const route = useRoute()
const id = String(route.params.id)
const cid = String(route.params.cid)

const errorText = (r: { error?: unknown }) => {
  const detail = (r.error as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string' ? detail : 'the API rejected the request'
}
const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

const cluster = ref<ClusterOut>()
const loading = ref(true)
const loadError = ref<string>()

const members = shallowRef<Member[]>([])
const total = ref(0)
const page = ref(1)
const membersError = ref<string>()
const membersLoading = ref(true)
let seq = 0

async function loadMembers() {
  const mine = ++seq
  membersLoading.value = true
  try {
    const r = await api.GET('/runs/{run_id}/clusters/{cluster_id}/members', {
      params: { path: { run_id: id, cluster_id: cid }, query: { offset: (page.value - 1) * PER_PAGE, limit: PER_PAGE } }
    })
    if (mine !== seq) return
    if (!r.data) throw new Error(errorText(r))
    members.value = r.data.items as Member[]
    total.value = r.data.total
    membersError.value = undefined
  } catch (e) {
    if (mine !== seq) return
    members.value = []
    membersError.value = message(e)
  } finally {
    if (mine === seq) membersLoading.value = false
  }
}
watch(page, loadMembers)

onMounted(async () => {
  loadMembers()
  try {
    const r = await api.GET('/runs/{run_id}/clusters/{cluster_id}', { params: { path: { run_id: id, cluster_id: cid } } })
    if (!r.data) throw new Error(errorText(r))
    cluster.value = r.data
  } catch (e) {
    loadError.value = message(e)
  } finally {
    loading.value = false
  }
})

const CARD_FIELDS = [['action', 'Action'], ['participants', 'Participants'], ['location', 'Location'], ['time', 'Time']] as const
const card = computed(() => (cluster.value?.cluster.card ? cardFields(cluster.value.cluster.card) : null))
</script>

<template>
  <div>
    <Crumbs class="mb-4" :items="[{ label: 'Runs', to: '/runs' }, { label: 'This run', to: `/runs/${id}` }, { label: 'All clusters', to: `/runs/${id}/clusters` }]" />
    <p v-if="loading" class="text-muted">
      Loading cluster...
    </p>
    <UAlert v-else-if="loadError || !cluster" color="error" variant="subtle" title="Could not load the cluster" :description="loadError" />
    <template v-else>
      <PageHeader :title="card?.action || 'Untitled event'" :description="`Cluster of ${cluster.cluster.size} mentions`" />
      <dl v-if="card" class="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-default bg-[var(--ui-border-muted)] sm:grid-cols-2 lg:grid-cols-4">
        <div v-for="[k, label] in CARD_FIELDS" :key="k" class="bg-default p-4">
          <dt class="text-sm text-muted">
            {{ label }}
          </dt>
          <dd class="mt-1 break-words font-semibold text-highlighted">
            {{ card[k] || '—' }}
          </dd>
        </div>
      </dl>
      <p v-else class="text-muted">
        No card for this cluster.
      </p>
    </template>

    <section class="mt-8 space-y-3">
      <h2 class="text-base font-bold text-highlighted">
        Mentions <span class="tabular font-normal text-muted">({{ total }})</span>
      </h2>
      <UAlert v-if="membersError" color="error" variant="subtle" title="Could not load members" :description="membersError" />
      <p v-else-if="membersLoading && !members.length" class="text-muted">
        Loading members...
      </p>
      <p v-else-if="!members.length" class="text-muted">
        This cluster has no members.
      </p>
      <article v-for="m in members" :key="m.mention_id" data-test="member" class="rounded-xl border border-default bg-default">
        <div class="space-y-3 p-5">
          <p :lang="m.language" class="font-serif text-[1.125rem] leading-[1.65] text-highlighted">
            <HighlightedText :text="m.sentence" :ranges="[m.trigger]" />
          </p>
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <NuxtLink :to="`/runs/${id}/documents/${m.document_id}`" class="mr-1 font-semibold text-primary hover:underline">
              {{ m.document_title }}
            </NuxtLink>
            <UBadge color="neutral" variant="outline" size="sm">{{ m.language }}</UBadge>
            <UBadge v-if="m.event_type" color="neutral" variant="soft" size="sm">{{ m.event_type }}</UBadge>
            <UBadge v-if="m.significance !== null" color="secondary" variant="soft" size="sm">significance {{ m.significance }}</UBadge>
          </div>
        </div>
        <div class="border-t border-default bg-muted px-5 py-4">
          <DecisionTable :decision="m.decision" :run-id="id" :cluster-id="cid" />
        </div>
      </article>
      <UPagination v-if="total > PER_PAGE" v-model:page="page" :items-per-page="PER_PAGE" :total="total" />
    </section>
  </div>
</template>
