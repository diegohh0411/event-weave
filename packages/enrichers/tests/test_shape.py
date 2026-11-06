from weave_enrichers.shape import Enrichment, enrichment_id


def test_enrichment_defaults_empty():
    e = Enrichment().model_dump()
    assert e["time"] == {"text": None, "spans": [], "confidence": None, "normalized": None}
    assert e["action"]["spans"] == []


def test_enrichment_id_stable_and_distinct():
    a = enrichment_id("m", "x", "1", None)
    assert a == enrichment_id("m", "x", "1", None) and len(a) == 32
    assert a != enrichment_id("m", "x", "1", "dep") and a != enrichment_id("m", "x", "2", None)
