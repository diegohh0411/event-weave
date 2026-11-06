# 04 · Mention Extractors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `weave_extractors` package with four interchangeable `extract`-stage adapters (`gold`, `stanza-rules`, `system-one`, `nuextract3`), a `tag` stage adapter (`significance`) and the `stanza` compose service, writing mentions and tags to their own tables.

**Architecture:** Each extractor is a core `StageAdapter` for `Stage.extract` (input unit `document`). It loads the document by id (same env-engine pattern as `ReplayProvider`), returns `StageResult(output={"mentions": [...], ...}, emits=[UnitRef(mention, ...)], rows={"mentions": [...]})`. Mention ids are content-addressed, so a cache hit in a later run points at rows the first run already wrote. Stanza runs as a small FastAPI service; adapters reach it through a `StanzaClient` protocol so tests use saved parse fixtures. Model calls go through `weave_inference.Inference.for_context(ctx)`; tests use the stub provider.

**Tech Stack:** Python 3.12, SQLAlchemy 2.1 + Alembic (core chain), Pydantic 2, FastAPI + httpx (stanza service/client), `stanza==1.10.1` (service image only, extra `stanza`), Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-mvp/04-mention-extractors.md` (with `00-overview.md`, `01-core.md`, `02-inference.md`, `03-document-providers.md`; prior plans `2026-11-05-0{1,2,3}-*.md`).

## Global Constraints

- Package `weave-extractors` in `packages/extractors`; depends on `weave-core` and `weave-inference` only, never on `weave-providers` (copy the five-language ISO map; do not import it). Adapters registered via entry-point group `weave.adapters`.
- Mention shape: document id, char span `[start, end)`, token span `[tok_start, tok_end)`, sentence index, text, extractor name, extractor version, probability (nullable). **`document.text[start:end] == text` for every emitted mention, every adapter.**
- `mention_id = sha256(f"{document_id}:{start}:{end}:{extractor}:{version}")[:32]`; `UnitRef.content_hash = sha256(f"{document.content_hash}:{start}:{end}:{extractor}:{version}")`.
- Emits are sorted by `(start, end)`; core appends the index to the document's order key (spec 01: document order key, then position).
- Gold extractor reads `gold_mentions` spans only (`start`, `end`, `text`) — never `mention_id`, `gold_pairs` or `gold_documents.topic/split`. No other adapter reads any `gold_*` table.
- Stanza languages and pinned models: `en, es, hi, tr, uk`, `stanza==1.10.1`, `resources_version="1.10.0"`, package `default`, models cached in volume `stanza-models` at `/models`. Service listens on `127.0.0.1` port mapping only.
- NuExtract3 triggers that cannot be located by exact match are dropped and counted (`output["unlocated"]`), never fuzzy-matched.
- Significance Noul prompt (verbatim): `"Is this a real-world occurrence that a newspaper would report as news?"`. Event-type Choice options (verbatim, this order): `conflict or attack, disaster or accident, politics or government, crime or justice, economy or business, health, science or technology, sports, culture, statement or communication, other`. Both live in one module (`tagging/questions.py`) so Diego can review them before the first full run.
- Tags never remove mentions: `tag` emits nothing new (`emits=None`), so turning it off changes no downstream job.
- No test touches the network or needs Stanza installed. Tests use the real Postgres from the core plan.

## Notes carried from earlier PRs

- PR #1: decide run **version pinning** before 03/04 real adapters merge — this plan bakes `version` into mention ids and cache keys; executed versions are already recorded by core. Raise with Diego in the PR description if per-run pinning is still wanted.
- PR #2: per-job cost averages include cache hits, so `nuextract3`/`system-one` estimates run low; long-running workers check readiness only at start. Not fixed here; repeat in this PR's "Known follow-ups".
- PR #3: crawl documents use a regex tokenizer. Stanza re-tokenizes for its own rules; mention token spans are computed against the **stored** `token_offsets` (Task 2 `char_to_token_span`), not Stanza's tokens. A mention whose chars do not align to stored token boundaries gets the covering token span.
- PR #3: `gold_pairs.split` conflict is a spec 07 concern; this plan does not read splits.

## Review Focus

1. Stanza's character offsets disagree with the stored text (Unicode normalisation, Hindi combining marks, Ukrainian apostrophes): a candidate whose `text[start:end]` differs from Stanza's word text is skipped and counted (`output["misaligned"]`), never emitted with a wrong span. (Tasks 3, 4)
2. A document longer than the System One deployment's context limit: candidates are split into several requests (`LimitExceeded` never reaches the job), and the answers still map back by question id. (Task 5)
3. NuExtract3 returns a trigger that occurs twice in its window, or returns non-JSON: first unused occurrence left-to-right; non-JSON counts the whole window as `unlocated_windows` and the job still succeeds. (Task 6)
4. A document with zero mentions: output `{"mentions": []}`, `emits=[]`, the run still finishes. (Task 2 contract suite)
5. Overlapping spans from one adapter (phrasal verb `"set up"` and `"set"`): the longer span wins, no duplicate mention ids. (Task 3)

---

## File structure

```
packages/extractors/pyproject.toml
packages/extractors/src/weave_extractors/
  shape.py              Mention model, mention_id, unit_for, char_to_token_span, load_document
  base.py               ExtractorBase: load doc → spans → validate → StageResult
  stanza_client.py      StanzaClient protocol, HttpStanzaClient, FixtureStanzaClient, Word/Sentence types
  stanza_service.py     FastAPI app (extra `stanza`): POST /parse, GET /health
  gold.py               GoldExtractor
  rules.py              StanzaRulesExtractor
  event_nouns.py        load_event_nouns(lang); build CLI
  data/event_nouns/{en,es,hi,tr,uk}.txt
  system_one.py         SystemOneExtractor
  nuextract.py          NuExtract3Extractor, locate()
  tagging/questions.py  SIGNIFICANCE, EVENT_TYPES (exact copy)
  tagging/adapter.py    SignificanceTagger
  cli.py                `weave extractors event-nouns`, `weave extractors report <run>`
packages/extractors/tests/{conftest.py, fixtures/stanza/*.json, fixtures/docs/*.json, test_*.py}
packages/core/src/weave_core/{schema,queue}.py
packages/core/src/weave_core/migrations/versions/0004_mentions.py
compose.yaml, Dockerfile (target `stanza`)
```

---

### Task 1: Core: `mentions` and `mention_tags` tables, rows allowlist

**Files:**
- Modify: `packages/core/src/weave_core/schema.py`, `packages/core/src/weave_core/queue.py:98-100`
- Create: `packages/core/src/weave_core/migrations/versions/0004_mentions.py`
- Test: `packages/core/tests/test_schema.py`, `packages/core/tests/test_queue.py`, `packages/api/tests/test_health.py` (head → `0004_mentions`)

**Interfaces:**
- Produces:
  - `mentions(id T PK, document_id T FK documents.id NOT NULL, start int, "end" int, tok_start int, tok_end int, sentence int, text T, extractor T, extractor_version T, probability float NULL)`, index on `document_id`.
  - `mention_tags(mention_id T FK mentions.id, tagger T, tagger_version T, deployment T, significance float NULL, event_type T NULL, event_type_probs JSONB NULL, PK(mention_id, tagger, tagger_version, deployment))`.
  - `queue.STAGE_ROW_TABLES = frozenset({"mentions", "mention_tags"})`; rows accepted when `name.startswith("gold_") or name in STAGE_ROW_TABLES`.

- [ ] **Step 1: Tests** — `test_mentions_tables_exist` (columns above); `test_complete_writes_mention_rows` (a fake extract result with `rows={"mentions":[...]}` inserts once; completing a second job with the same row inserts nothing); `test_rows_reject_unknown_table` still raises for `"documents"`.
- [ ] **Step 2:** Run `uv run pytest packages/core -k "mention or rows"` → FAIL.
- [ ] **Step 3:** Add tables, migration `0004_mentions` (down drops both), widen allowlist.
- [ ] **Step 4:** Run `uv run pytest packages/core packages/api` → all PASS.
- [ ] **Step 5:** Commit `feat(core): mentions and mention_tags tables`.

### Task 2: Package skeleton, mention shape, extractor base, contract suite, gold extractor

**Files:**
- Create: `packages/extractors/{pyproject.toml, src/weave_extractors/{__init__,shape,base,gold}.py}`, `tests/{conftest.py,contract.py,test_shape.py,test_gold.py}`; add package to root `pyproject.toml` workspace members.

**Interfaces:**
- Consumes: Task 1 tables; `weave_core.adapters.*`; env-engine pattern from `weave_providers/replay.py:52-58`.
- Produces:
  - `class Mention(BaseModel)`: `document_id, start, end, tok_start, tok_end, sentence, text, extractor, extractor_version, probability: float | None = None`; `.id -> str` (formula above).
  - `@dataclass Doc`: `id, language (ISO), text, content_hash, token_offsets: list[tuple[int,int]], sentence_offsets: list[tuple[int,int]]`; `load_document(doc_id: str) -> Doc` (maps full language names to ISO).
  - `char_to_token_span(doc: Doc, start: int, end: int) -> tuple[int, int, int]` → `(tok_start, tok_end, sentence)` of covering tokens.
  - `class ExtractorBase`: subclasses implement `spans(self, doc: Doc) -> SpanBatch` where `SpanBatch(spans: list[tuple[int,int,float|None]], extra: dict)`. `process()` loads the doc, drops spans failing `text[s:e]` check (counted in `extra["misaligned"]`), dedups overlaps (longest wins), sorts, builds `StageResult(output={"mentions":[m.model_dump()], **extra}, emits=[...], rows={"mentions": [...]})`. Override hook `load_document` for tests.
  - `GoldExtractor` spec: `name="gold", version="1", stage=extract, input_kind=document`, settings `{}`; reads `SELECT start, "end" FROM gold_mentions WHERE document_id=:id`.
  - `contract.py`: `run_contract(adapter_factory, docs: list[Doc])` — asserts slice equality, sorted emits, unique ids, empty doc → empty output; reused by Tasks 3–6.

- [ ] **Step 1: Tests** — `test_mention_id_stable_and_differs_by_version`; `test_char_to_token_span_covering`; `test_base_drops_misaligned_and_counts`; `test_base_overlap_longest_wins`; `test_gold_equals_gold_spans` (seed one document + 3 gold_mentions; output spans == gold, and `json.dumps(output)` contains none of the gold `mention_id`s); `test_gold_contract` via `run_contract`.
- [ ] **Step 2:** Run `uv run pytest packages/extractors` → FAIL (import).
- [ ] **Step 3:** Implement the interfaces above; entry point `gold = "weave_extractors.gold:GoldExtractor"`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(extractors): mention shape and gold extractor`.

### Task 3: Stanza service, client, event-noun lists, rules extractor

**Files:**
- Create: `stanza_client.py`, `stanza_service.py`, `event_nouns.py`, `rules.py`, `data/event_nouns/*.txt`, `tests/fixtures/stanza/*.json`, `tests/test_stanza_service.py`, `tests/test_rules.py`; modify `Dockerfile` (target `stanza`), `compose.yaml` (service `stanza`, volume `stanza-models`, `WEAVE_STANZA_URL=http://stanza:8001` on workers).

**Interfaces:**
- Produces:
  - `Word(text, start, end, upos, lemma, feats: str | None, deprel, head: int)`, `Sentence(words: list[Word])`.
  - `class StanzaClient(Protocol): def parse(self, text: str, lang: str) -> list[Sentence]`; `HttpStanzaClient(base_url)` → `POST /parse {"text","lang"}`; `FixtureStanzaClient(dir)` keyed by `sha256(lang+text)`.
  - Service: `create_app(model_dir: Path) -> FastAPI`, pipelines `tokenize,mwt,pos,lemma,depparse` (`mwt` only where the language has it), loaded lazily per language and kept; `GET /health` lists loaded languages. Offsets are `start_char/end_char` of each word.
  - `load_event_nouns(lang: str) -> frozenset[str]`; `weave extractors event-nouns --mcecr PATH --stanza-url URL` writes lowercase lemmas of NOUN tokens inside **train-split** gold triggers, one per line, sorted. (Data files are derived lemma lists, not dataset text.)
  - `StanzaRulesExtractor`: `name="stanza-rules", version="1"`, settings `{stanza_url: str | None = None}` (defaults to `WEAVE_STANZA_URL`). Rules: `upos == "VERB"` and `deprel not in {"aux","aux:pass","cop"}`; `upos == "NOUN"` and `lemma.lower() in event_nouns(lang)`; phrasal particle: a `deprel == "compound:prt"` child extends its head verb's span to the particle's end only when contiguous. Probability `None`.

- [ ] **Step 1: Tests** — with a fixture parse of `"Police said the protest was broken up after the attack."`: mentions are `said`, `protest` (if in en nouns fixture), `broken up`, `attack`; `was` excluded (aux). `test_unknown_language_fails_non_retryable` (`AdapterFailure(retryable=False)`). `test_service_parse_shape` with a monkeypatched fake pipeline (no stanza import). Contract suite.
- [ ] **Step 2:** Run `uv run pytest packages/extractors -k "rules or stanza"` → FAIL.
- [ ] **Step 3:** Implement. Service image: `FROM base AS stanza`, `uv sync --extra stanza`, `CMD uvicorn weave_extractors.stanza_service:app --port 8001`.
- [ ] **Step 4:** Run → PASS; `podman compose config` validates.
- [ ] **Step 5:** Generate noun lists against a running service (`podman compose up -d stanza`; `weave extractors event-nouns ...`), record per-language counts in commit message; commit `feat(extractors): stanza service and rules extractor`.

### Task 4: Shared candidate generator

**Files:** Create `candidates.py`, `tests/test_candidates.py`.

**Interfaces:**
- Produces: `candidates(doc: Doc, sentences: list[Sentence]) -> list[Candidate]`, `Candidate(start, end, sentence, text)`: every word with `upos` in `{VERB, NOUN, ADJ}` (not `PROPN`) whose `lemma.lower()` is not in `STOPWORDS[lang]` (a small list per language in the module), offsets checked against `doc.text` (misaligned skipped and counted via returned `misaligned: int`).
- `mark(sentence_text: str, rel_start: int, rel_end: int) -> str` wraps the candidate as `⟦word⟧`.

- [ ] **Step 1: Tests** — candidate set for the Task 3 fixture; a Ukrainian fixture with `’` where Stanza offsets are shifted by one → candidate skipped, `misaligned == 1`; `mark` output exact.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Commit `feat(extractors): candidate generator`.

### Task 5: System One extractor

**Files:** Create `system_one.py`, `tests/test_system_one.py`.

**Interfaces:**
- Consumes: Task 4 `candidates`, `mark`; `Inference.for_context(ctx).system_one(SystemOneRequest)`; `Noul`; `LimitExceeded`.
- Produces: `SystemOneExtractor`: `name="system-one", version="1"`, `capabilities={system_one}`, settings `{threshold: float (required, 0..1), stanza_url: str | None = None, batch: int = 64}`. State: `{"title": doc.title, "definition": EVENT_DEFINITION}`; one `Noul(id=f"c{i}", prompt=marked_sentence)` per candidate. Batches of `batch` questions; on `LimitExceeded` halve the batch and retry that slice (down to 1, then fail non-retryable). Mention when `value >= threshold`, `probability = value`. `output["candidates"] = [[start, end, p], ...]` (every candidate, for threshold sweeps in spec 07).
- `EVENT_DEFINITION` (in module): written from MCECR's annotation section — any word or short phrase naming something that happens or holds (verbs, event nouns, some adjectives), including reporting verbs such as "said". Diego reviews the wording before the first full run.

- [ ] **Step 1: Tests** — stub script scores `c0=0.9, c1=0.2`, threshold 0.5 → one mention with `probability == 0.9`; `test_splits_on_limit` (deployment with `limits.context_tokens` small enough to force ≥2 requests; all candidates answered; ledger has ≥2 rows); `test_threshold_required` (settings without threshold fail validation); contract suite with the stub.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Commit `feat(extractors): system one extractor`.

> Threshold tuning on English dev needs trigger P/R/F1 from spec 07. This plan records `threshold` in the frozen run config (required setting) and stores every candidate's probability so 07 can sweep it without new model calls.

### Task 6: NuExtract3 extractor

**Files:** Create `nuextract.py`, `tests/test_nuextract.py`.

**Interfaces:**
- Consumes: `Inference.chat(ChatRequest)` on capability `llm`.
- Produces: `NuExtract3Extractor`: `name="nuextract3", version="1"`, `capabilities={llm}`, settings `{window_sentences: int = 3}`. Windows: non-overlapping runs of `window_sentences` sentences from `doc.sentence_offsets`. Template arg `{"event_triggers": ["verbatim-string"]}`, instructions include `EVENT_DEFINITION` from Task 5. `locate(window_text: str, triggers: list[str]) -> tuple[list[tuple[int,int]], int]` — exact `str.find`, left to right, each next search after the previous hit of the same string; returns spans and unlocated count. Output: `unlocated: int`, `unlocated_windows: int` (non-JSON responses).

- [ ] **Step 1: Tests** — `locate("he said and said", ["said","said","told"]) == ([(3,7),(12,16)], 1)`; non-JSON stub reply → `unlocated_windows == 1`, job succeeds; contract suite with scripted stub replies.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Commit `feat(extractors): nuextract3 extractor`.

### Task 7: Significance and event-type tagger

**Files:** Create `tagging/{questions,adapter}.py`, `tests/test_tagging.py`.

**Interfaces:**
- Consumes: `upstream[Stage.extract]["mentions"]` (find own mention by `unit.id`); `load_document` for the sentence.
- Produces: `SignificanceTagger`: `stage=tag, input_kind=mention, name="significance", version="1"`, `capabilities={system_one}`. One request per mention: state = marked sentence + title; questions `Noul(id="significance", prompt=SIGNIFICANCE)`, `Choice(id="event_type", prompt=..., options=EVENT_TYPES)`. Returns `StageResult(output={"significance", "event_type", "event_type_probs"}, emits=None, rows={"mention_tags":[...]})`.

- [ ] **Step 1: Tests** — exact `EVENT_TYPES` list and `SIGNIFICANCE` string; tag row written per mention; `test_tag_off_same_downstream`: two stub-provider runs over the same 2 docs, one with `tag`, one without → identical set of `enrich` jobs `(unit_id, unit_content_hash)`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Commit `feat(extractors): significance tagger`.

### Task 8: Run report and end-to-end check

**Files:** Create `cli.py`, `tests/test_report.py`, `tests/test_pipeline.py`; mount `extractors` Typer group in `weave_core/cli.py` the same way `crawl` is mounted.

**Interfaces:**
- Produces: `extract_report(engine, run_id) -> dict` summing over the run's `extract` outputs: `documents`, `mentions`, `misaligned`, `unlocated`, `unlocated_windows`, `failed_jobs`; `weave extractors report <run_id>` prints it as a table.

- [ ] **Step 1: Tests** — pipeline test: `mcecr`-shaped fixture docs via `replay` → each of the four extractors (stanza via fixture client, models via stub) → run finishes, `mentions` rows match outputs, every row passes the slice check against `documents.text`. Report counts match.
- [ ] **Step 2–4:** Fail → implement → pass. Full suite: `uv run pytest` → all PASS.
- [ ] **Step 5:** Commit `feat(extractors): extraction report`.

---

## Spec coverage

| Spec item | Task |
|---|---|
| Mention shape, exact spans | 1, 2 |
| Gold (spans only, no ids) | 2 |
| Stanza rules + service + pinned models + event-noun list | 3 |
| System One (candidates, Noul, batching by context, threshold in config) | 4, 5 |
| NuExtract3 (windows, exact locate, counted drops) | 6 |
| Significance + type; stored, never filters | 7 |
| Contract tests for all four adapters with stub | 2–6 (`contract.py`) |
| Unlocated triggers in run report | 8 |
| Trigger P/R/F1 on English dev, threshold choice | Deferred to spec 07 (outputs keep candidate probabilities) |
