# 03 · Document providers

**Status:** Draft for review · **Needs:** 01 · [Overview](00-overview.md)

## Goal

Get natural text that contains event mentions into the `documents` table in one shape, whatever its source: the MCECR dataset, a live crawl of news sites, or documents already stored by an earlier run.

## In and out

**In:** the document shape, the MCECR provider (including writing gold), the crawl4ai crawler, the stored-document replay provider, document order.

**Out:** finding mentions (04). Using gold (07).

## Rules

**One document shape.** Every provider produces: a stable external ID, language, title, byline, outlet, URL (when there is one), publication time (when known), the text, the character offsets of its tokens and sentences, an order key and a content hash. Stages only ever see this shape. They never learn which provider a document came from, its topic, or its split.

**Order key.** Each provider sets the order in which documents enter clustering:

- MCECR: a seeded interleave across topics, so consecutive documents rarely share a topic. Within a topic, documents keep their file order.
- Crawl: publication time, then URL.
- Replay: the original documents' order keys.

**MCECR provider.**

- Reads `<language>/events/<topic>.json` for the selected languages. Each file is one topic's documents as tokens with sentence and document indexes, labels and mention IDs.
- Rebuilds each document's text from its tokens and stores the character offset of every token, so a span in tokens and a span in characters always point at the same words.
- Takes a topic's split (train, dev, test) from the `withinDoc/` and `crossDoc/` pair files, which may hold either a JSON array or one record per line. A topic that appears in no pair file has no split; it is listed in the run's report and excluded from split-based selections.
- Selection: languages, splits, an optional topic limit (for small slices) and the seed.
- Writes gold mentions (spans and mention IDs) to the gold tables and gold pairs for spec 07 to build clusters from. Gold is never written to `documents` or `mentions`.
- Records the hash of every file it read.
- The dataset path is a setting; the data is mounted read-only into the containers and never copied into the repo.

**Crawler (crawl4ai).**

- Configured with a list of outlets. For each: name, domain, language, its news sitemap and/or RSS URLs, and an optional limit of articles per day.
- A crawl has a time window (default: the last 7 days). URLs come from the sitemaps and feeds within the window, never from following links.
- Each article is fetched with crawl4ai, which extracts the main text, title, byline and publication date; the date from the sitemap or feed is used when the page has none.
- Respects robots.txt, with a per-domain rate limit (default 1 request per 2 seconds) and an identifying user agent that names no person.
- Removes duplicates by canonical URL, then by content hash.
- Keeps the raw HTML, compressed, so articles can be re-extracted later without fetching them again.
- A page that fails, is paywalled or has no extractable body is recorded with the reason, never stored as an empty document.
- Crawled text stays in the local database; nothing crawled is committed to the repo.

The MVP demo list is 4–6 English and Spanish outlets chosen by Diego, for example Reuters, AP, BBC, El País, El Universal and EFE, subject to their robots.txt.

**Replay provider.** Feeds documents already in the database (for example, a past crawl) into a new run, so runs over crawled news are repeatable without crawling again.

## Contract

A provider declares `name`, `version` and a settings schema. Given its settings and the run seed, it yields documents in the shape above, in order. `weave crawl <outlets.yaml> --days 7` runs a crawl outside any run and stores the documents for replay.

## Acceptance

- For every MCECR gold mention, the stored document text sliced at the mention's character span equals the mention's text.
- Document, token and sentence counts per language match the dataset files, and the documents-per-language numbers match the paper's data-collection statistics (English 2,600, Spanish 694, Turkish 193, Ukrainian 1,123, Hindi 1,192).
- Both formats of the pair files are read, and the topics without a split are listed.
- No column of `documents` reveals topic, split or gold.
- The same seed gives the same MCECR order; a different seed gives a different one.
- A crawl of the same outlets and window twice adds no duplicate documents.
- A URL disallowed by robots.txt is never fetched.
- Crawler tests use saved HTML fixtures per outlet and make no network calls.
