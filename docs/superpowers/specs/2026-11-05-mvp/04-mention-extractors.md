# 04 · Mention extractors

**Status:** Draft for review · **Needs:** 02, 03 · [Overview](00-overview.md)

## Goal

Find the event mentions (triggers) in a document, with exact spans, using any of four interchangeable adapters; then tag each mention with how significant it is and what kind of event it is.

## In and out

**In:** the mention shape; the gold, Stanza-rules, System One and NuExtract3 extractors; significance and event-type tagging.

**Out:** describing a mention (05). Scoring extraction (07).

## Rules

**Mention shape.** Document, character span, token span, sentence index, text, the extractor's name and version, and the extractor's probability when it has one. Spans always match the document text exactly.

**What counts as an event.** For the MCECR runs, extraction follows MCECR's own notion: any word or short phrase that names something that happens or holds (verbs, event nouns, some adjectives), including reporting verbs such as "said". The definition and examples given to the System One and NuExtract3 adapters are written from the paper's annotation section and checked against MCECR train triggers. Narrowing to "significant" events is not done here (see significance below).

**Adapters.**

- **Gold** (Track 1 only). Emits exactly the MCECR gold spans for the document, with no mention IDs or clusters. It reads gold spans and nothing else.
- **Stanza rules.** Uses the `stanza` service (models pinned per language: en, es, hi, tr, uk). Proposes verbs that are not auxiliaries or copulas, and nouns whose lemma appears in an event-noun list built from MCECR train triggers per language. Multi-word triggers follow MCECR's `B-`/`I-` pattern only where a rule says so (for example, phrasal-verb particles).
- **System One.** Stanza proposes a broad candidate set (every verb, noun and adjective that is not a stopword). The state is the document title and the candidate's sentence with the candidate marked; for each candidate one Noul asks whether it names an event in MCECR's sense. All candidates of a document go in as few requests as the deployment's context allows. A candidate is a mention when its Noul value is at or above a threshold tuned on English dev.
- **NuExtract3.** Through an `llm` deployment, with a template that asks for a list of verbatim event triggers, one sentence window at a time. Each returned trigger is located in its window by exact match; a trigger that cannot be located is dropped and counted, never guessed.

**Significance and event type.** A separate tagging step runs on every mention, whichever extractor produced it, through a `system_one` deployment:

- a Noul: "Is this a real-world occurrence that a newspaper would report as news?",
- a Choice for event type: conflict or attack, disaster or accident, politics or government, crime or justice, economy or business, health, science or technology, sports, culture, statement or communication, other.

The values are stored on the mention. They are used to filter what the UI shows and to describe the crawl demo. They never remove mentions before clustering or scoring. The wording of both questions and the type list are reviewed by Diego before the first full run.

## Contract

An extractor adapter follows the stage contract (01) with the document as its input unit and a list of mentions as output. Tagging is its own stage with the mention as input unit and can be turned off in a run.

## Acceptance

- For every adapter, every emitted span slices the document text to exactly the mention's text.
- The gold extractor's output equals the gold spans and carries no gold IDs or cluster information.
- On MCECR English dev, each adapter reports trigger precision, recall and F1 by exact span match (computed by spec 07).
- The System One threshold is chosen on English dev and recorded in the run configuration.
- NuExtract3 triggers that cannot be located are counted in the run report.
- Significance and type values exist for every mention when tagging is on, and turning tagging off changes no clustering or score.
- Contract tests run the same suite against all four adapters using the stub provider.
