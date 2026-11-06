# weave-clusterer (spec 06)

Embeds enriched mentions (`embedding-gemma`) and clusters them in order (`threshold`, `system-one`).

    weave clusterer report <run> [--k N]

prints a run summary (mentions, clustered, failed, clusters, singletons, decision calls, reasons) and retrieval
recall@K per language. Recall reads gold, so it is evaluation-only. `--k` below the recorded K re-slices the stored
candidates, so sweeping K needs no re-run.

## Tuning procedure

K, field weights and thresholds are tuned by CoNLL F1 on English dev. The scorer comes from spec 07, so it is not
built here. Once it lands:

1. Run the `threshold` adapter over a grid of K, weights and thresholds. It makes no model calls, so the grid is free.
   Use `weave clusterer report` to check recall@K, and read the best CoNLL F1 setting off the scorer.
2. Fix the weights and K that win, then run `system-one` at that K, sweeping its Noul threshold.
3. Record the chosen `k`, `weights` and `threshold` in the run config. They have no defaults.

## Known follow-ups

- Cost estimates include cache hits.
- Workers check readiness only at start.
- Migrate the enrichers to `Inference.deployment()` (they still read `Inference._catalog`).
