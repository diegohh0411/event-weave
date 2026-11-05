# Event Weave MVP: Overview

**Status:** Draft for review · **Date:** 2026-11-05 · **Owner:** Diego Hernández Herrera

Index spec for the first version of Event Weave, a pipeline that reads news, finds event mentions, describes each one by what happened, who took part, where and when, and groups mentions of the same real-world event across documents and languages. Each numbered spec is sized so one implementer can plan and build it alone.

## 1. Purpose

Two goals, in order:

1. **The claim.** Show that a pipeline built on System One models (decision models such as Jev, lfm2-d1, Clef or Mammoth, which return typed answers with calibrated probabilities instead of text) clusters event mentions more accurately than the models in the MCECR paper (Pouran Ben Veyseh et al., Findings of NAACL 2024), at a cost we can measure and defend.
2. **The product seed.** The same code later becomes an event coreference service for newspapers and governments. Every part is product code, not a benchmark script.

**Success for the MVP:**

1. On MCECR, Diego can open a page that shows our scores beside the paper's, per language, with confidence intervals and the cost per 1,000 mentions.
2. A crawl of a week of news from a handful of English and Spanish outlets produces event clusters that span outlets and languages, browsable in the UI.
3. Swapping any stage's implementation (Stanza for an LLM, Clairic for a System One enricher, one System One model for another) is a change in the run builder, not in code.

## 2. How the pieces fit

```
Provider ──▶ documents ──▶ Extractor ──▶ mentions ──▶ Enricher ──▶ enrichments ──▶ Embedder ──▶ member vectors
 (03)                       (04)                       (05)                          (06)            │
                                                                                                     ▼
                                               clusters + members + decisions ◀── Clusterer (06) ◀───┘
 every model call ──▶ ledger (02)          Evaluation (07) reads clusters + gold      API + web (08)
```

All stages share one core (01): runs, a job queue in Postgres, cached stage outputs, and an ordered writer for clustering. All model calls go through one inference layer (02), which knows which providers serve which models and records every call's cost.

## 3. The rules, in plain words

1. **Two tracks.**
   - *Track 1, paper-comparable:* gold mentions go in, clustering happens inside each topic, the paper's test split and metrics are used. This is the setting the paper reports, so it gives the headline comparison.
   - *Track 2, realistic:* predicted mentions, one index for the whole run, no topic boundaries, all languages together. The paper has no number here; this is what a client would get.
2. **No stage sees the answers.** Gold clusters are read only by the scorer. In Track 1, the gold extractor passes gold mention spans and nothing else.
3. **Tune on English dev only.** K, thresholds and weights are chosen on MCECR English dev. Everything else is zero-shot, which makes non-English results strictly comparable to the paper's transfer tables and conservative against its monolingual ones.
4. **Clusters keep their members.** A cluster is never rewritten by a model. Its card (the four fields shown to the decision model) is built in code from its members, so joining costs no model call and every decision can be audited.
5. **No reranker.** The decision model reads the mention and all K candidate cards in one call, which already does the reranker's job.
6. **Every model call is costed.** Cost per stage comes from provider usage records, never from guesses; estimates are labelled as estimates.
7. **Nothing is dropped silently.** Failed items are counted and shown next to every score. Event significance is stored on each mention for filtering in the UI, never used to drop mentions before scoring.
8. **Same run, same result.** A run's configuration is frozen when it starts, model answers are cached, and clustering follows a fixed order, so re-running gives the same clusters.

## 4. The data, as found on 2026-11-05

MCECR lives in `~/Documents/crossdoc_event_coreference` (not in this repo). Per language: `events/<topic>.json` (pre-tokenized documents of one topic, with `B-Event`/`I-Event` labels and mention IDs) and `withinDoc/` and `crossDoc/` train/dev/test files of mention pairs with a 0/1 label. Some split files hold one JSON record per line.

| | English | Spanish | Hindi | Turkish | Ukrainian |
|---|---|---|---|---|---|
| Topics | 603 | 179 | 327 | 50 | 295 |
| Documents | 2,600 | 694 | 1,192 | 193 | 1,123 |
| Gold mentions (counted) | 22,424 | 1,628 | 7,086 | 8,422 | 24,260 |

Gold links never cross languages. Spanish is sparsely annotated (about 2 mentions per document against 6–44 elsewhere). The paper evaluates with gold mentions, within topics, with MUC, B³, CEAF-e and BLANC, and reports no cost.

## 5. Decisions

| Decision | Status | Reason |
|---|---|---|
| MVP = MCECR benchmark **and** a news-crawl demo | Decided (Diego, 2026-11-05) | Prove the claim and show the product |
| One machine, parallel stage workers, Postgres + pgvector, all in containers via compose | Decided (Diego, 2026-11-05) | Hours, not days, for all of MCECR, with no extra infrastructure |
| Python monorepo; FastAPI + Nuxt 4 for the API and web, as in the current research app | Decided (Diego, 2026-11-05) | All models and crawl4ai are Python; same stack Diego already runs |
| Clusters keep every member; cards are derived in code | Decided (Diego, 2026-11-05) | No drift, no extra model cost, full audit trail |
| Two evaluation tracks | Decided (Diego, 2026-11-05) | Apples-to-apples with the paper, plus the realistic number |
| Report only our own cost for now; a re-run of a paper baseline on Hugging Face Jobs may come later | Decided (Diego, 2026-11-05) | The paper reports no cost; our ledger leaves room for a baseline column |
| No reranker | Decided (Diego, 2026-11-05) | The decision model reads all candidates jointly and is cheap |
| Embeddings: EmbeddingGemma 2 | Decided (Diego, 2026-11-05) | Multilingual, small, runs locally |
| Enrichment adapters: Clairic (hosted, closed weights), NuExtract3 (open weights), a System One candidate-choice enricher | Decided (Diego, 2026-11-05) | Clairic for the claim, an open alternative, and a System One version |
| Models, providers and deployments are separate; stages ask for a capability, runs pick a deployment; OpenRouter is the default provider | Decided (Diego, 2026-11-05) | The same model can be served by several providers at different settings |
| llama.cpp runs natively on the Mac host, not in a container | Decided (Diego, 2026-11-05) | Containers on macOS cannot use Metal |
| Crawl demo: 4–6 named English and Spanish outlets, last 7 days, seeded from news sitemaps or RSS | Decided (Diego, 2026-11-05) | Clean URLs and dates, bounded and repeatable |
| Event significance is a stored Noul + event-type Choice, never a filter before scoring | Decided (Diego, 2026-11-05) | MCECR annotates every event; clients care about significant ones |
| Clustering order for MCECR Track 2: seeded interleave across topics | Proposed | MCECR has no dates; interleaving avoids an easy topic-by-topic order |
| Score report for Track 2 treats mentions present on only one side the way the reference CoNLL scorer does | Proposed | One standard, stated once |

## 6. Specs and order

| # | Spec | Delivers | Needs |
|---|---|---|---|
| 01 | [Core](01-core.md) | Repo, compose, schema, runs, stage queue, output caching, ordered writer | — |
| 02 | [Inference](02-inference.md) | Models, providers, deployments, discovery, wire clients, ledger, stub provider | 01 |
| 03 | [Document providers](03-document-providers.md) | MCECR, crawl4ai crawler, stored-document replay | 01 |
| 04 | [Mention extractors](04-mention-extractors.md) | Gold, Stanza rules, System One, NuExtract3; significance tagging | 02, 03 |
| 05 | [Enrichers](05-enrichers.md) | Clairic, NuExtract3, System One candidate choice | 02, 04 |
| 06 | [Clusterer](06-clusterer.md) | Member vectors, retrieval, card builder, decision adapters, partitions | 02, 05 |
| 07 | [Evaluation](07-evaluation.md) | Gold clusters, splits, scorer, bootstrap, cost report, paper table | 01, 03 |
| 08 | [API and web](08-api-and-web.md) | Run builder, runs, clusters browser, compare, document view | 01 (with mocks), then the rest |

03 and 07 can be built in parallel once 01 and 02 land. 08 can start on mocks right after 01.

**Smallest useful release:** 01, 02, 03 (MCECR only), 04 (gold extractor), 05 (one enricher), 06 and 07. That gives the first Track 1 number against the paper. The crawler, the other extractors, Track 2 and the web app follow.

## 7. Out of scope for the MVP

- Authentication and multi-user access. Everything listens on localhost only.
- Editing clusters by hand, and annotating new gold.
- A parallel clustering writer (needed for the product's throughput later).
- Re-running a paper baseline to price it.
- Deploying anywhere other than Diego's Mac.
