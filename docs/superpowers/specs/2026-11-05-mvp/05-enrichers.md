# 05 · Enrichers

**Status:** Draft for review · **Needs:** 02, 04 · [Overview](00-overview.md)

## Goal

Describe every mention with four fields taken from its own document, as in X-AMR: the **action** (what happened), the **participants** (who or what took part), the **location** (where) and the **time** (when). Three interchangeable adapters do it.

## In and out

**In:** the enrichment shape; the Clairic, NuExtract3 and System One adapters; field coverage reporting.

**Out:** embedding the fields and using them to cluster (06).

## Rules

**Enrichment shape.** For each mention, each of the four fields holds:

- `text`: the value, or nothing when the document does not state it,
- `spans`: where the text comes from in the document (participants can have several spans),
- `confidence`: the adapter's confidence, when it gives one,
- for time only, `normalized`: an ISO date or interval when the adapter can produce one, using the document's publication time to resolve expressions such as "on Monday".

A field is never filled with something the document does not say. Text that does not match the document exactly is either located by exact match or discarded and counted.

**What each adapter sees.** The document's title, outlet and publication time, and the text around the mention (its sentence plus a window of neighbouring sentences, size set per adapter and chosen on English dev), with the mention's trigger marked. The marked trigger tells the adapter which event to describe when a sentence holds several.

**Adapters.**

- **Clairic** (`extraction` capability, Cloudflare Workers AI). Typed text fields with instructions written for each field and for "the event marked in the text". Every answer is checked against the source text as in the research app's Clairic client: an answer that is not an exact passage of the document is discarded. As many mentions of a document per request as the model's field limit allows.
- **NuExtract3** (`llm` capability). A template with `action`, `participants` (a list), `location` and `time` as verbatim strings, plus `time_normalized` as a date-time; non-reasoning mode; instructions name the marked event.
- **System One** (`system_one` capability). It cannot write text, so it chooses among candidates:
  - Stanza proposes candidates in the window: named entities, the noun phrases attached to the trigger (subject, object, oblique), and time expressions.
  - Participants: one Noul per candidate, "Is this a participant in the marked event?"; every candidate at or above the threshold is kept.
  - Location and time: one Choice each over the candidates of the right kind, plus a companion Noul "Does the text state where (when) the marked event happened?". Below the Noul threshold the field is empty.
  - Action: the trigger plus its attached predicate words from the dependency parse, built in code with no model call.
  - All questions for a document's mentions are packed into as few requests as the deployment allows. For a deployment that decides a request's questions jointly (Clef), each mention gets its own request.
  - It cannot normalize time; `normalized` stays empty.

**No gold for arguments.** MCECR has no gold for these fields. Enrichers are judged by what they do to clustering scores (07) and by coverage: the share of mentions with each field filled, per adapter and language. The UI (08) shows a random sample of enrichments next to their sentences for spot checks.

## Contract

An enricher follows the stage contract (01) with the mention as its input unit (adapters may batch the mentions of one document) and the enrichment above as output.

## Acceptance

- Every non-empty field's spans slice the document to text that matches the field.
- No adapter fills a field with text absent from the document; discarded answers are counted per adapter.
- Coverage per field, adapter and language appears in the run report.
- The System One adapter makes no model call for the action field, and never sends a Choice larger than the deployment's option limit.
- Window sizes and thresholds come from English dev and are recorded in the run configuration.
- Contract tests run the same suite against all three adapters using the stub provider.
