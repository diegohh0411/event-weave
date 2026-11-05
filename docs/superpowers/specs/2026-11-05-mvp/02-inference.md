# 02 · Inference

**Status:** Draft for review · **Needs:** 01 · [Overview](00-overview.md)

## Goal

One layer that every stage uses to call a model. It knows which providers serve which models with which settings, checks that they are actually available, speaks each wire format once, and records the cost of every call.

## In and out

**In:** models, providers and deployments; discovery and readiness; the wire clients; rate limits and retries; the ledger; cost estimates; the stub provider.

**Out:** what a stage asks a model (04–06). Choosing deployments for a run is done in the run configuration (01, 08).

## Rules

**Three concepts, kept separate.**

- **Model:** a canonical identity and its capability: `lfm2-d1` (`system_one`), `jev-1.14.0` (`system_one`), `nuextract3` (`llm`), `embeddinggemma-2` (`embeddings`), `clairic` (`extraction`).
- **Provider:** an endpoint, a credential reference (the name of an environment variable, never the secret), the wire formats it speaks and its rate limits. Examples: OpenRouter, TypeSafe, Cloudflare Workers AI, Liquid, Baseten, a local llama.cpp server.
- **Deployment:** one model on one provider with specific settings: quantization, context limits, the maximum number of options a Choice may have, whether it decides a request's questions jointly or one by one, price per input and output token, and rate limits.

A model can have many deployments. Runs and ledger rows always point at a deployment, so "lfm2-d1 on OpenRouter" and "lfm2-d1 Q8 on local llama.cpp" are compared as separate things.

**Capabilities.**

| Capability | Meaning | Example deployments |
|---|---|---|
| `system_one` | Typed questions (Choice, Score, Noul) about a state; returns answers with probabilities | Jev on TypeSafe; lfm2-d1, openjev, Clef on llama.cpp `/v1/systemone`; System One models on OpenRouter; Clef on Cloudflare |
| `llm` | Text generation through an OpenAI-compatible chat API | NuExtract3 on llama.cpp or vLLM; any chat model on OpenRouter |
| `embeddings` | Vectors for text | EmbeddingGemma 2 on llama.cpp or OpenRouter |
| `extraction` | Typed field extraction with exact source spans and confidence | Clairic on Cloudflare Workers AI |

A stage adapter asks for a capability. The run decides which deployment serves it.

**Configuration.** Providers and deployments are declared in a version-controlled `inference.yaml` (with secrets read from `.env`). OpenRouter is the default provider for `system_one`, `llm` and `embeddings` when a run names no deployment.

**Discovery and readiness.** On start-up and on demand, each provider is probed:

- llama.cpp: `GET /models`; a model is a decision model only if `architecture.output_modalities` contains `decisions`. A plain language model is never treated as `system_one`.
- OpenRouter: the models and endpoints listings, including the `decisions` output modality for System One models.
- TypeSafe: `GET /v1/models`.
- Cloudflare: the model is listed for the account.

Each deployment is `ready`, or `not ready` with a reason (unreachable, model missing, capability not advertised, credential missing). A run cannot start with a deployment that is not ready. Readiness, the server's reported version and the model's reported ID are stored with the run. Wire formats for Liquid, Baseten and any provider not listed above are checked against their documentation when they are added, never assumed to match another provider's.

**Wire clients.** One client per wire format, shared by all stages:

- System One (`POST /v1/systemone`): TypeSafe and llama.cpp. Questions of type `choice`, `score` and `noul`; state as text or JSON.
- OpenRouter decisions.
- Cloudflare Workers AI (`/ai/run/<model>`): Clairic and Clef.
- OpenAI-compatible chat, with extra template arguments where a model needs them (NuExtract3's `template` and `instructions`).
- Embeddings.

Clients enforce each deployment's limits before sending: context size, Choice option count, request size. A request over a limit is refused locally with a clear error, not sent.

**Rate limits and retries.** Per-deployment concurrency and requests-per-second limits are respected. `429` responses honour `retry-after`. Transient errors retry with backoff; the stage's retry policy (01) applies on top.

**Ledger.** Every call writes one row: run, stage, adapter, deployment, request hash, input tokens, output tokens, cached tokens if reported, cost, latency, status. Cost comes from the provider's reported usage and price when available; otherwise it is computed from the deployment's prices and flagged as estimated. Local deployments record $0 API spend and their wall time.

**Cost estimates.** Before a run starts, its cost is estimated from the ledger's average cost per unit for each adapter and deployment, times the number of units not already cached. With no history, the estimate says so instead of inventing a number.

**Stub provider.** A provider that serves every capability with scripted, deterministic answers (configurable per test). It makes the whole pipeline runnable in CI at no cost.

**Language note.** TypeSafe documents Jev as English-first, with lower accuracy in other languages. Results are always reported per language, and the deployment used is shown next to every score.

## Contract

A stage calls the layer with a capability-typed request and the run's deployment for that capability, and receives a typed response with usage and the deployment's ID, or a typed error (limit exceeded, not ready, rate limited, provider error). `weave inference probe` prints every deployment's readiness; `weave inference smoke <deployment>` sends one real request and is never run in CI.

## Acceptance

- A llama.cpp server running a plain language model is reported as `llm`, never as `system_one`, and a run that asks it for System One answers is refused before it starts.
- The same model declared on two providers appears as two deployments with separate ledger rows and costs.
- A Choice with more options than the deployment allows is refused locally.
- Every model call made by any stage appears in the ledger exactly once, with its run and deployment.
- A `429` with `retry-after` is retried after the stated delay.
- The full pipeline runs end to end against the stub provider with no network access.
- Probing shows a missing credential as `not ready: credential missing`, naming the variable.
