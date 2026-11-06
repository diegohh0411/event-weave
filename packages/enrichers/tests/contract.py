"""The contract every enricher adapter satisfies (spec 05 acceptance)."""
from helpers import unit_for
from weave_core.stages import Stage

FIELDS = ("action", "participants", "location", "time")


def _run(factory, doc, mention):
    unit = unit_for(mention, doc)
    result = factory(doc).process(unit, {Stage.extract: {"mentions": [mention]}})
    assert result.emits == [unit]  # the mention is forwarded unchanged
    enrichment = result.output["enrichment"]
    assert set(enrichment) == set(FIELDS)  # every field present
    for name in FIELDS:
        field = enrichment[name]
        assert {"text", "spans", "confidence"} <= set(field)
        assert all(doc.text[s:e] for s, e in field["spans"])
        assert field["text"] == ("; ".join(doc.text[s:e] for s, e in field["spans"]) if field["spans"] else None)
    assert result.rows["enrichments"][0]["mention_id"] == unit.id
    return enrichment


def run_contract(factory, cases, empty_cases=()):
    """factory(doc) -> adapter serving `doc`. `cases` are (doc, mention); `empty_cases` are ones where the
    model answers nothing and so must produce an all-empty enrichment."""
    for doc, mention in cases:
        _run(factory, doc, mention)
    for doc, mention in empty_cases:
        enrichment = _run(factory, doc, mention)
        for name in FIELDS:
            assert enrichment[name]["text"] is None and enrichment[name]["spans"] == []
        assert enrichment["time"]["normalized"] is None
