# Fixture sources

Reached docs (fetched 2026-11-05):
- Cloudflare: https://developers.cloudflare.com/api/resources/ai/subresources/models/methods/list/
  (GET /accounts/{account_id}/ai/models/search; envelope `{success, errors, messages, result[]}`; the docs leave
  `result` items as `unknown`, so item fields `name` and `task.name` follow Workers AI's model schema from memory, not verified).
- TypeSafe: https://docs.typesafe.ai/api/system-one (says "Available names are returned by GET /v1/models";
  requests go to https://api.typesafe.ai/v1/systemone, so base_url `https://api.typesafe.ai` in inference.yaml is consistent).
  The /v1/models response body is not documented there; fixture assumes the OpenAI-style `{data:[{id}]}`.

Not reached / minimal fixtures matching the spec, NOT copied from vendor docs:
- llama.cpp: `/models` with `architecture.output_modalities` (as stated in spec 02).
- OpenRouter: `/models` and `/embeddings/models` with `architecture.output_modalities` incl. `decisions`
  (docs page for the endpoints listing 404'd). The per-model endpoints listing is not used (one probe per provider).
