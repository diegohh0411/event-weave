# Event Weave

A pipeline that reads news, finds event mentions, describes each one by its action, participants, location and time, and groups mentions of the same real-world event across documents and languages.

Every stage has interchangeable implementations:

| Stage | Implementations |
|---|---|
| Document provider | MCECR dataset, crawl4ai news crawler, stored-document replay |
| Mention extractor | Gold (benchmark only), Stanza rules, System One, NuExtract3 |
| Mention enrichment | Clairic, NuExtract3, System One |
| Mention clustering | EmbeddingGemma 2 retrieval + System One decision, or a similarity threshold |

The first goal is to show, on the [MCECR](https://aclanthology.org/2024.findings-naacl.245/) benchmark, that a pipeline built on System One decision models clusters event mentions more accurately than the paper's models, at a measured cost.

Status: design. The MVP specs are in [`docs/superpowers/specs/2026-11-05-mvp/`](docs/superpowers/specs/2026-11-05-mvp/00-overview.md).

## Quick start

```bash
podman compose up -d --build
curl http://127.0.0.1:8000/health   # {"status":"ok","schema":"0008_crawls"}
open http://localhost:3000
```

The complete crawl app starts with one command and no required setup: compose builds the images, starts Postgres, applies migrations through the API, then starts the frontend and the long-lived crawl worker (`weave crawl-worker`) after the API is healthy.

- Frontend: http://localhost:3000 (API published on `127.0.0.1:8000`; override with `WEAVE_WEB_PORT` / `WEAVE_API_PORT`).
- No `.env`, outlet file, MCECR dataset, manual migration or host process is required.
- The API starts without probing model providers; deployments are reported not ready until an explicit probe (frontend "Re-probe deployments", or `podman compose exec api weave inference probe`). Missing credentials never block health or crawling.

## Crawls

From the frontend at http://localhost:3000, submit public sites/domains in the Crawls page: one or more normalized public domains, a lookback in days and a per-site article limit, with optional explicit RSS/Atom feeds or sitemaps.

- Crawling itself requires no inference credentials; a submitted crawl is queued and consumed by the long-lived `crawler` worker (`podman compose exec crawler weave crawl /app/outlets.yaml` runs an ad-hoc crawl).
- A crawl that finds no usable source (robots-blocked, unreachable or no declaration) is reported per source with the discovery error; an empty time window is distinguished from a failed discovery.
- Articles that turn out to be all paywalled finish with zero stored documents; the result explains the paywall instead of reporting a generic success.

Crawl job status (`queued`, `running`, `completed`, `failed`), progress counters, sources and per-article outcomes persist in Postgres and survive worker restarts and page refresh.

## Enabling inference

Model credentials are optional environment variables (`OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`) named by the inference catalog (`WEAVE_INFERENCE_FILE`, mounted read-only at `/app/inference.yaml` into the API and pipeline workers). Leaving them empty keeps crawling available and reports deployments not ready; set real values and run the deployment probe to make a deployment ready and launch inference.

## Dataset and host model servers

The MCECR dataset is opt-in: the default compose stack mounts no dataset, and the override file `deploy/compose/mcecr.yaml` mounts a host checkout read-only into the pipeline workers (`podman compose -f compose.yaml -f deploy/compose/mcecr.yaml up -d`). Host model servers (llama.cpp) run on the host and are reached from containers at `http://host.containers.internal:<port>`.

## Development

Backend tests: `WEAVE_TEST_DATABASE_URL=postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres uv run pytest packages/<package>`.
