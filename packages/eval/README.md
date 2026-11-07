# weave-eval

## Reference fixtures

`tests/fixtures/reference/*.json` are small key/response clusterings (`{"documents": [{"id", "key", "response"}]}`,
mention -> cluster label). `expected.json` holds what the reference CoNLL scorer prints for each of them
(MUC, B-cubed, CEAF-e, BLANC, plus BLANC link counts). The scorer is not vendored; the file was produced with
`conll/reference-coreference-scorers` at tag **v8.01** (perl 5):

    git clone --branch v8.01 https://github.com/conll/reference-coreference-scorers /path/to/refscorer
    python packages/eval/tools/reference_scores.py /path/to/refscorer

Each fixture document becomes one `#begin document` in a CoNLL key file and a response file (one token per
mention), scored as `perl scorer.pl <metric> key response none` (corpus totals). `pradhan2014*` is the worked
example `{a} {bc} {def}` from the scorer's own test suite (cases A2, A3, A4); `test_pradhan_documented_values` also
checks the values documented there. Regenerate `expected.json` only when a fixture changes.
