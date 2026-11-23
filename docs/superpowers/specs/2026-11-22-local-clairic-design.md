# Local Clairic inference

Date: 2026-11-22

## Intent

Cloudflare released the weights for Clairic (27B) and Clairic Flash (~10B) on Hugging Face
(`Cloudflare/clairic`, `Cloudflare/clairic-flash`, Apache-2.0). Today the `clairic` enricher can
only reach Clairic through Cloudflare's API (`clairic@cloudflare`), which has never been run for
real. Running Clairic Flash locally makes the enricher usable without an API account or per-call
cost, and gives runs a reproducible, pinned model.

## Goals

1. A local `clairic` service that loads Cloudflare's release code and weights once and answers
   extraction requests over HTTP.
2. It runs **natively on the Mac**, on Apple's GPU backend (`device="mps"`), outside the
   containers. Podman containers on macOS can't use the GPU. Compose services reach it at
   `host.containers.internal`, as they already do for `llamacpp-local`.
3. A new provider kind, `clairic-local`, with a client in
   `packages/inference/src/weave_inference/clients/` that maps our `ExtractionRequest` /
   `ExtractionResponse` to the service. `ClairicEnricher` does not change.
4. Catalog entries in `inference.yaml`: a `clairic-flash` model (`capability: extraction`), the
   `clairic-flash@local` deployment, and a `clairic-local` provider with `rate: {concurrency: 1}`.
5. Answers keep Clairic's per-field `confidence` and character spans, so the enricher's existing
   ranking and exact-quote check work unchanged.
6. A measured `max_fields` for the local deployment, found by testing rather than guessed.

## Non-goals

- The 27B Clairic locally. Its BF16 weights (~56 GB) don't fit in this Mac's 36 GB. The cached
  community GGUF (`Xylolabs/clairic-GGUF`, Q4_K_M) runs in llama.cpp but lacks Cloudflare's typed
  head, so it has no confidence scores or spans. Not part of this work.
- Running the service in a container or on CUDA. The service code shouldn't prevent it
  (device is a setting), but no compose service or Linux image is added now.
- Images, PDFs, estimates, and forecasts. The enricher only extracts text spans.
- Changing what the enricher asks for or how it batches mentions.

## Anti-goals

- Silently falling back to Cloudflare's API, or to a different model, when the local service is
  down. A run that names `clairic-flash@local` fails visibly instead.
- Recording results from the local model under the `clairic@cloudflare` deployment name, or
  sharing a cache with it. The deployment is part of the cache key already; keep it that way.

## Design

### Service: `weave_enrichers.clairic_service`

Follows `weave_extractors/stanza_service.py`: a FastAPI app behind an optional extra
(`clairic = ["torch", "transformers", "xgrammar", "huggingface_hub", "fastapi", "uvicorn"]`,
versions pinned to the release's `requirements.txt`).

- On startup: `snapshot_download(repo, revision=<pinned commit>)`, put the snapshot on
  `sys.path`, `load_release_model(path, device=...)`. Repo, revision and device come from env
  (`WEAVE_CLAIRIC_REPO`, default `Cloudflare/clairic-flash`; `WEAVE_CLAIRIC_REVISION`;
  `WEAVE_CLAIRIC_DEVICE`, default `mps`). Pinning the revision keeps runs reproducible.
- `POST /extract`: body `{text, fields}` (our wire shape). The service builds a Clairic request:
  `state = text`, and each field becomes
  `{"type": "list", "items": "text", "instructions": <our instruction>, "source_only": ...}`
  so every field may return several quoted spans. Answers are translated back to
  `{fields: {name: [{text, start, end, confidence}]}, usage}`. Evidence without a span
  (ambiguous match) is dropped and counted, like any span the enricher can't map.
- `GET /health`: model id, revision, device, and whether the model is loaded.
- One request at a time. Clairic raises a queue-full error on simultaneous requests; the
  service holds a lock and returns 503 if a second request arrives while one is running
  (the client retries per the provider's rate settings).
- Responses report `truncated`; a truncated input is an error, not a partial answer, since
  the enricher can't tell which mentions lost context.

Started with `uvicorn ... weave_enrichers.clairic_service:app --factory --port 8002` in its own
environment (`services/clairic/requirements.txt`), as the README describes. The release pins
torch 2.13 and transformers 5.10.2, which conflict with the workspace lock, so it is not a workspace
extra.

### Client: `clients/clairic_local.py`

A `WireClient` like `CloudflareClient`: `build` posts `{text, fields}` to `<base_url>/extract`;
`parse` reads `fields` into `Span`s and `usage` into `Usage`. Registered in `gateway._WIRE` as
`(Capability.extraction, "clairic-local")`, and `clairic-local` added to `ProviderKind`.

### Catalog

```yaml
models:
  clairic-flash: {capability: extraction}
providers:
  clairic-local:
    kind: clairic-local
    base_url: http://host.containers.internal:8002
    rate: {concurrency: 1}
deployments:
  clairic-flash@local: {model: clairic-flash, provider: clairic-local, local: true,
                        limits: {max_fields: <measured>}}
```

## Findings (2026-11-22, revision 36ad68b, M4 Max 36 GB)

- List-of-text answers carry `value: [str]`, per-item `confidences`, a list-level `confidence`,
  and `spans: [[start, end] | null]`. The service uses per-item confidence, and for a null span
  falls back to the quote's single occurrence in the text (Clairic's own rule).
- The runtime installs Triton/CUDA kernels only for bfloat16 weights. In float16 it keeps stock
  Transformers ops and runs on `mps` (with `PYTORCH_ENABLE_MPS_FALLBACK=1`). Load: ~40 s.
- Each field's prompt includes the whole schema, so cost grows faster than linearly: one mention
  (4 fields) takes ~34 s, five mentions (20 fields) ~449 s. `max_fields` is set to 4.
- All 20 answers in the five-mention test were correct.
- The gateway used httpx's 5 s default timeout for every provider; providers now have a
  `timeout` setting (default 5 s, 300 s for `clairic-local`).

## Testing

- Client: unit tests against recorded service responses (no model needed), like the existing
  client tests.
- Service: request/response translation tested with a fake `systemone`; the real model is
  exercised by one opt-in test marked to skip unless `WEAVE_CLAIRIC_URL` is set.
- End to end: a small run with `enrich: clairic` and `deployments: {extraction:
  clairic-flash@local}` on a handful of documents, checked in the web app's spot-check page.
