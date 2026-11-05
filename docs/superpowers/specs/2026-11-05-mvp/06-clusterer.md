# 06 · Clusterer

**Status:** Draft for review · **Needs:** 02, 05 · [Overview](00-overview.md)

## Goal

Place each enriched mention, one at a time and in order, into an existing event cluster or a new one. Clusters are indexed by the same four fields as mentions: action, participants, location and time. Retrieval finds the K most similar clusters, and a decision model picks one of them or says none.

## In and out

**In:** member vectors, retrieval, cluster cards, the System One and threshold decision adapters, the decision record, tuning.

**Out:** the ordered writer and partitions (01). Scoring (07).

## Rules

**Clusters keep their members.** A cluster is the set of its member mentions with their enrichments. Nothing about a cluster is ever written by a model. Its card and its retrieval vectors are derived from the members.

**Member vectors.** Each member gets up to four vectors from an `embeddings` deployment (EmbeddingGemma 2 by default): one per non-empty field. The text embedded per field is the field value, using the model's recommended prefix for retrieval documents and queries. The checkpoint and dimension are pinned in the run configuration. Embedding is a normal parallel stage; only the decision is ordered.

**Retrieval.** For a new mention, within its partition only:

- for each of its non-empty fields, find the nearest member vectors of the same field (pgvector),
- a cluster's score for a field is its best member's similarity, so a large cluster is matched by its closest member, not by an average,
- a cluster's overall score is a weighted sum over the fields both sides have, with the weights renormalized when a field is missing on either side,
- keep the top K clusters. K is a setting tuned on English dev and capped by the decision deployment's option limit.

With no clusters yet in the partition, the mention starts a new cluster with no decision call.

**Cluster card.** Built in code each time a member joins, with no model call:

- the representative member: the one with the highest average similarity to the others,
- for each field, the representative's value followed by the other distinct values, most frequent first, up to a limit,
- member count, languages, earliest and latest publication time, and the representative's sentence.

Cards have a size limit so that K cards fit the deployment's context.

**Decision adapters.**

- **System One.** The state holds the new mention (its marked sentence, its four fields, its document's title and publication time) and the K candidate cards under neutral labels (`c1`…`cK`, so cluster IDs never reach the model). Two questions in one request:
  - a Noul, "Does any candidate describe the same real-world event as the mention?",
  - a Choice, "Which candidate describes the same real-world event?", with one option per card.
  The mention joins the chosen cluster when the Noul value is at or above a threshold tuned on English dev; otherwise it starts a new cluster. This follows TypeSafe's advice to use a companion Noul instead of a "none of the above" option.
- **Threshold** (baseline). Join the top-scoring cluster when its retrieval score is at or above a threshold tuned on English dev; otherwise start a new cluster. No model call.

**Decision record.** For every mention: the candidates shown (cluster IDs, retrieval scores, card hashes), the model's probabilities and Noul value when there was a call, the outcome, the adapter and the deployment. The UI shows this as "why it joined" (08).

**Same-document mentions.** Mentions of one document go in order like any others and may join each other's clusters, which covers within-document coreference.

**Tuning.** K, the field weights and the thresholds are chosen on MCECR English dev with gold mentions, maximizing CoNLL F1, and recorded in the run configuration. Retrieval recall at K (how often a gold-coreferent mention's cluster is among the K retrieved) is reported for the chosen K.

## Contract

The clusterer runs inside the ordered writer (01): for each mention in order it reads the partition's current clusters, makes at most one decision call, and commits the mention's membership, the updated card and the decision record in one transaction. Decision answers are cached like any stage output, so re-running a run replays the same decisions.

## Acceptance

- Every mention that reaches clustering ends in exactly one cluster; failed mentions are counted.
- Retrieval never returns a cluster from another partition.
- No request sent to a decision model contains a cluster ID, topic, split or gold information.
- K never exceeds the decision deployment's option limit, and cards never push a request over its context limit.
- Retrieval recall at the chosen K is reported on English dev.
- Re-running a finished run gives identical clusters with no new decision calls.
- The threshold baseline runs with no model calls at all in the clustering stage.
- A cluster's card changes only when a member joins, and is the same whenever it is rebuilt from the same members.
