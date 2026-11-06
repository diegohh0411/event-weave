from weave_enrichers.shape import Enrichment, enrichment_id


def test_enrichment_defaults_empty():
    e = Enrichment().model_dump()
    assert e["time"] == {"text": None, "spans": [], "confidence": None, "normalized": None}
    assert e["action"]["spans"] == []


def test_enrichment_id_stable_and_distinct():
    a = enrichment_id("m", "x", "1", None, "h")
    assert a == enrichment_id("m", "x", "1", None, "h") and len(a) == 32
    assert a != enrichment_id("m", "x", "1", "dep", "h") and a != enrichment_id("m", "x", "2", None, "h")


def test_enrichment_id_depends_on_settings_hash():
    assert enrichment_id("m", "x", "1", None, "a") != enrichment_id("m", "x", "1", None, "b")
