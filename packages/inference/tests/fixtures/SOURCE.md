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

## Task 5 (wire request/response fixtures), fetched 2026-11-05
Doc-backed:
- `typesafe/systemone_response.json`: https://docs.typesafe.ai/api/system-one (request `{model, state, questions:{name:{type, instructions, criteria}}}`;
  answers keyed by question name with `choice|noul|score` values, `probabilities`, `usage.{input_tokens,output_tokens}`).
  llama.cpp `/v1/systemone` is assumed to match TypeSafe.
- `openai/*.json`: OpenAI chat-completions / embeddings public shapes (`choices[0].message.content`, `usage.prompt_tokens_details.cached_tokens`, `data[].embedding/index`).
  NuExtract3 `template`/`instructions` via `chat_template_kwargs` per the task brief; not re-verified against NuExtract docs.
ASSUMED (vendor docs unreachable or schema-less; minimal fixtures):
- `openrouter/decisions_response.json`: path `/decisions` and TypeSafe-like body; `usage.cost` as reported cost.
- `cloudflare/systemone_response.json`, `cloudflare/extraction_response.json`: `/ai/run/<model>` envelope `{success, result}`;
  Clef/Clairic body and result shapes (Cloudflare docs show only `response` for text-generation JSON mode).
- Score questions: TypeSafe criteria is an ordered list of 1..10 levels; we map Score(low, high) to integer levels low..high and add `low` back to the returned 0-based score.
