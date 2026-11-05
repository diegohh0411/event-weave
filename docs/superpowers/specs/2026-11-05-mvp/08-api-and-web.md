# 08 · API and web

**Status:** Draft for review · **Needs:** 01 (mocks), then 02–07 · [Overview](00-overview.md)

## Goal

A small web app on Diego's Mac to configure runs by picking an implementation for each stage, watch them run, browse the clusters they produce, and compare runs with each other and with the MCECR paper.

## In and out

**In:** the FastAPI service and the Nuxt 4 + Nuxt UI app, their pages, mocks for front-end work.

**Out:** authentication, editing clusters, annotating gold, public deployment.

## Rules

**Same stack as the research app.** FastAPI for the API, Nuxt 4 with Nuxt UI for the web, Vitest for front-end tests, a mock server so the web can be built before the backend is done.

**Localhost only.** Both containers bind to localhost. There is no login.

**Pages.**

1. **Run builder.**
   - Pick a document provider and its selection (MCECR languages, splits, topic limit; or a stored crawl).
   - Pick one adapter per stage. Each adapter shows its settings form, built from its settings schema, and, when it needs a model, a deployment picker that lists only ready deployments with the right capability.
   - Pick a track and a seed.
   - Before launch, show how many units already have cached outputs and the estimated cost (02), or say that no estimate exists yet.
2. **Runs.** Per run: status, progress per stage (queued, running, done, failed), dollars spent so far, mentions per hour, and buttons to cancel and resume. A failed item shows its error.
3. **Clusters browser.** Search by text; filter by language, size, event type and significance; sort by size or recency. A cluster page shows its card, its members with each trigger highlighted in its sentence and a link to the document, and for each member why it joined: the candidates it was shown, their retrieval scores, the model's probabilities and the Noul value.
4. **Compare.** Pick runs. A table per track and language: MUC, B³, CEAF-e, BLANC, CoNLL F1 with intervals; trigger precision, recall and F1 for Track 2; dollars per 1,000 mentions; mentions per hour; failures. For Track 1 the paper's numbers sit in their own clearly labelled rows.
5. **Document view.** One document with every mention highlighted and coloured by cluster, and its four fields on hover. This is the main view for the crawl demo.
6. **Enrichment spot check.** A random sample of enrichments next to their sentences, per adapter, for quick manual review (05).

**API.** Endpoints for: adapters and their settings schemas; deployments and their readiness; runs (create, validate, estimate, start, cancel, resume, progress); clusters (search, detail with members and decisions); documents with mentions and enrichments; scores and the comparison. Responses never include gold, except inside score reports.

## Contract

The API is the only way the web reaches data. Every list endpoint pages its results. The OpenAPI schema is served by FastAPI and the web's mock server follows it.

## Acceptance

- A run configured in the builder produces the same configuration hash as the same configuration given to `weave run create`.
- The deployment picker never offers a deployment that is not ready or lacks the capability the adapter needs.
- A cluster page shows, for every member, the decision that placed it there.
- The compare table shows the paper's rows only for Track 1, labelled as the paper's.
- The document view colours two mentions alike only when they are in the same cluster.
- The web runs against the mock server with no backend, and its tests pass in CI.
