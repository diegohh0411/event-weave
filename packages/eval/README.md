# weave-eval

## Reference fixtures

`tests/fixtures/reference/*.json` are small key/response clusterings (`{"documents": [{"id", "key", "response"}]}`,
mention -> cluster label). `expected.json` holds what the reference CoNLL scorer prints for each of them
(MUC, B-cubed, CEAF-e, BLANC, plus BLANC link counts). The scorer is not vendored; the file was produced with
`conll/reference-coreference-scorers` at tag **v8.01** (commit `61e8e660e2c5154a56ac830ba2f33a99a76edf53`, perl 5):

    git clone --branch v8.01 https://github.com/conll/reference-coreference-scorers /path/to/refscorer
    python packages/eval/tools/reference_scores.py /path/to/refscorer

Each fixture document becomes one `#begin document` in a CoNLL key file and a response file (one token per
mention), scored as `perl scorer.pl <metric> key response none` (corpus totals). `pradhan2014*` is the worked
example `{a} {bc} {def}` from the scorer's own test suite (cases A2, A3, A4); `test_pradhan_documented_values` also
checks the values documented there. Regenerate `expected.json` only when a fixture changes.

## Scoring a run

    weave eval <run_id> [--split test] [--resamples 10000] [--workers N]

Scores a finished run (any other state is refused, exit 1), stores one `evaluations` row plus its
`evaluation_scores`, prints the report and ends with `evaluation <uuid>`. The bootstrap seed is the run config's
`seed`; the same run, split and resamples give the same intervals whatever `--workers` is.

## Reading the report

Sections, in order:

1. **Scores**: P, R and F1 per setting and language, with a 95% topic-bootstrap interval of F1. Track 1 (`paper`
   runs) has `within` and `cross`; Track 2 (`realistic` runs) has `pooled` per language and `all`, then trigger
   scores. CoNLL F1 has no P/R. Hindi cross-document is not scored; its reason is printed in its place.
2. **Failures**: failed jobs per configured stage, zero-filled. Mentions whose cluster job failed count as missed gold.
3. **Chain statistics**: ours / paper's annotation statistics, `≠` marking each mismatch, with the notes
   (Hindi gold chains are within-document only).
4. **Comparison with the paper** (Track 1): the paper prints F1 only, and "monolingual" rows are harder for us
   (we tune on English dev only), "transfer" rows are like for like. Numbers are never merged or subtracted.
   `UNCHECKED` heads the report until `mcecr_paper_v1.yaml` has two distinct `checked_by` names.
5. **Track 2 mention policy** (Track 2) and cluster spread.
6. **Significance**: the paper published no outputs, so there is no paired test.
7. **Cost**: per stage and deployment; `*` marks estimated dollars.

## English dev tuning loop

Run the clusterer grid (see `packages/clusterer/README.md`), then for each grid run:

    weave eval <run> --split dev --resamples 200

and compare the English `within`/`cross` CoNLL F1 across the runs. Keep the best K, weights and thresholds, then
report once on `--split test` with the default resamples.
