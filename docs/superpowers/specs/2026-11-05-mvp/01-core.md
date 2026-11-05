# 01 · Core

**Status:** Draft for review · **Needs:** — · [Overview](00-overview.md)

## Goal

The skeleton every other spec plugs into: the repo layout, the containers, the database, what a run is, how stage work is queued and cached, and the ordered writer that clustering needs.

## In and out

**In:** repo and packaging, `compose.yaml`, the database schema and migrations, runs, the stage contract, the job queue, output caching, partitions and the ordered writer, the `weave` command line.

**Out:** model calls (02), what any stage actually does (03–06), scoring (07), HTTP endpoints and pages (08).

## Rules

**Repo.** One Python monorepo managed with uv workspaces: a package per concern (core, inference, providers, extractors, enrichers, clusterer, evaluation, api), plus a `web/` Nuxt 4 app. A stage package depends on core and inference, never on another stage package.

**Containers.** `compose.yaml` runs, on podman compose or docker compose alike:

| Service | Role |
|---|---|
| `postgres` | Postgres with pgvector; the only stateful service |
| `api` | FastAPI (spec 08) |
| `web` | Nuxt 4 + Nuxt UI (spec 08) |
| `worker` | Stage workers; one image, the stage to run is a setting, scaled per stage |
| `crawler` | crawl4ai and its headless browser (spec 03) |
| `stanza` | Stanza pipelines behind a small HTTP service, models pinned and cached in a volume |

llama.cpp is **not** a container on the Mac: it runs natively so it can use Metal, and containers reach it at `host.containers.internal` (or `host.docker.internal`). It is configured as a provider like any other (spec 02). Ports bind to localhost only. Secrets live in a git-ignored `.env`; `.env.example` documents every variable.

**Runs.** A run is one complete configuration:

- a document provider and its selection (spec 03),
- one adapter per stage, each with its settings and, where it calls a model, a deployment (spec 02),
- a track (`paper` or `realistic`), which sets the clustering partition,
- a seed.

The configuration is validated and hashed when the run is created and frozen when it starts. Results, scores and ledger rows always carry the run ID. A run can be cancelled and resumed; resuming continues where it stopped.

**Stage contract.** Every adapter of every stage declares:

- `name`, `version` (bumped whenever its prompts, questions, rules or parsing change),
- a settings schema with defaults,
- the capabilities it needs (`llm`, `system_one`, `embeddings`, `extraction`, or none),
- its input unit (document or mention) and output type,
- its maximum concurrency (a ceiling; the deployment's rate limit also applies).

It receives one input unit plus read-only access to that unit's upstream outputs, and returns one output or a typed failure. It never reads gold, other runs, or another partition.

**Cached outputs.** A stage output is stored under a key built from the input's content hash, the adapter name and version, its settings, and the deployment. A new run that needs an output with the same key reuses it and makes no model call. This is what lets runs that differ only in the clusterer share extraction and enrichment, and what keeps the ledger from charging one call twice.

**Queue.** Each stage has job rows `(run, stage, unit)` in Postgres, claimed with `FOR UPDATE SKIP LOCKED`. A job becomes ready when its upstream outputs exist. Failures retry with exponential backoff (default 3 attempts); after that the job is `failed`, with its error, and the run continues. Workers send heartbeats; a job whose worker stops heartbeating is released.

**Partitions and the ordered writer.** Clustering depends on order, so it does not use the plain queue:

- Each run is split into partitions: one per topic for the `paper` track, one for the whole run for the `realistic` track.
- Each partition has exactly one writer at a time, held with a Postgres advisory lock. Different partitions run in parallel.
- Every mention has an order key: the provider's document order key (spec 03), then the mention's position in the document.
- The writer takes mentions strictly in order. If the next mention's upstream outputs are not ready, it waits; if they failed, it skips that mention and records it as failed.

**Determinism.** Given the same run configuration, the same cached outputs and the same recorded model answers, the clusters are identical.

## Data

Tables, at the level of what they hold (the implementer chooses columns and indexes):

- `runs`: configuration, its hash, status, timestamps, counts.
- `documents`: provider, stable external ID, language, title, byline, outlet, URL, publication time, text, token offsets, order key, content hash.
- `mentions`, `enrichments`, `member_vectors` (pgvector), `clusters`, `cluster_members`, `decisions`: owned by specs 04–06.
- `stage_outputs`: cache key → output reference.
- `jobs`: run, stage, unit, status, attempts, error, heartbeat.
- `ledger`: owned by spec 02. `scores`: owned by spec 07.

Gold lives in its own tables, written by the MCECR provider and read by the scorer only (spec 07).

Migrations are versioned and run on `api` start-up.

## Contract

The `weave` command line, available inside the `worker` image:

- `weave run create <config.yaml>` validates, prints the hash and an estimated cost (spec 02), and creates the run.
- `weave run start|cancel|resume|status <run>`.
- `weave worker --stage <stage>` runs a worker pool for one stage.
- `weave db migrate`.

The API (spec 08) offers the same operations over HTTP.

## Acceptance

- `podman compose up` on a clean checkout brings up every service, and `weave db migrate` succeeds.
- A run whose configuration is invalid (unknown adapter, missing deployment, a deployment without the capability the adapter needs) is refused with a message that names the problem.
- Two runs that differ only in the clusterer reuse every upstream output: the second run makes no extraction or enrichment calls.
- Killing a worker mid-job leaves the job to be picked up by another worker, with no duplicate output.
- A failing adapter marks its jobs `failed` after the retry limit and the rest of the run completes.
- In a `paper` run, mentions of two different topics never share a partition; in a `realistic` run, all mentions share one.
- The ordered writer never processes a mention before an earlier one in the same partition.
- Running the same configuration twice with cached answers gives identical clusters.
