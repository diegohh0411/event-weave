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
