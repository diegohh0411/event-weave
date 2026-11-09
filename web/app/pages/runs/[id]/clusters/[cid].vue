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

const card = computed(() => (cluster.value?.cluster.card ? cardFields(cluster.value.cluster.card) : null))
</script>

<template>
  <div class="space-y-6">
    <NuxtLink :to="`/runs/${id}/clusters`" class="text-sm text-primary hover:underline">
      All clusters
    </NuxtLink>
    <p v-if="loading" class="text-muted">
      Loading cluster...
    </p>
    <UAlert v-else-if="loadError || !cluster" color="error" variant="subtle" title="Could not load the cluster" :description="loadError" />
    <UCard v-else>
      <template #header>
        <h1 class="font-semibold">
          Cluster of {{ cluster.cluster.size }} mentions
        </h1>
      </template>
      <dl v-if="card" class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <dt class="text-sm text-muted">
            Action
          </dt>
          <dd>{{ card.action || '—' }}</dd>
        </div>
        <div>
          <dt class="text-sm text-muted">
            Participants
          </dt>
          <dd class="break-words">
            {{ card.participants || '—' }}
          </dd>
        </div>
        <div>
          <dt class="text-sm text-muted">
            Location
          </dt>
          <dd>{{ card.location || '—' }}</dd>
        </div>
        <div>
          <dt class="text-sm text-muted">
            Time
          </dt>
          <dd>{{ card.time || '—' }}</dd>
        </div>
      </dl>
      <p v-else class="text-muted">
        No card for this cluster.
      </p>
    </UCard>

    <section class="space-y-3">
      <h2 class="font-semibold">
        Members ({{ total }})
      </h2>
      <UAlert v-if="membersError" color="error" variant="subtle" title="Could not load members" :description="membersError" />
      <p v-else-if="membersLoading && !members.length" class="text-muted">
        Loading members...
      </p>
      <p v-else-if="!members.length" class="text-muted">
        This cluster has no members.
      </p>
      <UCard v-for="m in members" :key="m.mention_id" data-test="member">
        <div class="space-y-3">
          <p>
            <HighlightedText :text="m.sentence" :ranges="[m.trigger]" />
          </p>
          <p class="text-sm text-muted">
            <NuxtLink :to="`/runs/${id}/documents/${m.document_id}`" class="text-primary hover:underline">
              {{ m.document_title }}
            </NuxtLink>
            · {{ m.language }}<template v-if="m.event_type">
              · {{ m.event_type }}
            </template><template v-if="m.significance !== null">
              · significance {{ m.significance }}
            </template>
          </p>
          <DecisionTable :decision="m.decision" :run-id="id" :cluster-id="cid" />
        </div>
      </UCard>
      <UPagination v-if="total > PER_PAGE" v-model:page="page" :items-per-page="PER_PAGE" :total="total" />
    </section>
  </div>
</template>
