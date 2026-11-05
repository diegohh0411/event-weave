# 07 · Evaluation

**Status:** Draft for review · **Needs:** 01, 03 · [Overview](00-overview.md)

## Goal

Turn a finished run into numbers that can be put next to the MCECR paper's: coreference scores per language with confidence intervals, trigger scores for Track 2, and the run's cost and speed.

## In and out

**In:** gold clusters, splits, the scorer, both tracks' scoring rules, bootstrap intervals, the paper's numbers, the cost report, storing scores.

**Out:** pages that show them (08).

## Rules

**Gold clusters.** Built from the gold pairs written by the MCECR provider (03): mentions linked by a positive pair are joined transitively within their language; every gold mention left unlinked is a singleton. The scorer is the only reader of gold.

**Check against the paper.** Per language, the number of chains, non-singleton chains, and within- and cross-document coreference links are compared with the paper's annotation statistics (for example, English: 20,040 chains, 1,280 non-singleton). A mismatch is shown on the report, explained in the spec's follow-up, and never hidden. Small differences in mention counts are already known (for example, English 22,424 counted against 22,445 reported).

**Metrics.** MUC, B³, CEAF-e and BLANC, each as precision, recall and F1, plus CoNLL F1 (the mean of MUC, B³ and CEAF-e F1). The implementation is checked against the reference CoNLL scorer.

**Track 1 (paper-comparable).** Gold mentions; test split; per language. Two settings, as in the paper:

- *within-document:* each document's mentions scored as their own clustering,
- *cross-document:* each topic's mentions scored as one clustering.

Scores are summed over documents or topics as the reference scorer does over a corpus.

**Track 2 (realistic).** Predicted mentions in one pooled clustering per run.

- Trigger detection: precision, recall and F1 by exact token span.
- Coreference: the system clustering against gold over the test split, with mentions present on only one side handled the way the reference CoNLL scorer handles differing mention sets. This choice is stated on every Track 2 report.
- Also reported: how many system clusters contain mentions from more than one gold topic, and how many contain more than one language.

**Confidence intervals.** 95% intervals from 10,000 bootstrap resamples of topics, per language and track: each resample scores only the sampled topics' mentions. The paper published no system outputs, so no paired test against it is possible; the report says so.

**The paper's numbers.** Tables 4–7 of the paper (within- and cross-document, monolingual and English-to-target transfer) are transcribed into a versioned data file with the source and page, checked twice, and shown beside ours. Our runs are tuned on English dev only, so the report labels the comparison: against the transfer tables it is like for like; against the monolingual tables our setting is the harder one.

**Cost and speed.** From the ledger (02), per stage and deployment: calls, input and output tokens, dollars (marking estimated values), latency at the median and 95th percentile; for the run: dollars per 1,000 mentions, mentions per hour of wall time, and the share of upstream outputs that came from cache.

**Failures.** Every report shows the number of documents and mentions that failed at each stage next to the scores.

**Stored scores.** Scores carry the run ID, the run configuration hash, the hash of the gold they used and the scorer version.

## Contract

`weave eval <run>` scores a finished run and stores the result; the API (08) serves stored scores and the comparison with the paper. Scoring a run that has not finished is refused.

## Acceptance

- Scoring gold against itself gives 100 on every metric.
- On fixture clusterings, the scorer matches the reference CoNLL scorer to within 0.01.
- The gold chain counts per language are shown beside the paper's annotation statistics.
- The same run and seed give the same intervals.
- A Track 2 report states its treatment of mentions present on only one side.
- The paper's numbers file cites its source, and the compare view never mixes it up with our results.
- The cost report's dollar total equals the sum of the run's ledger rows.
