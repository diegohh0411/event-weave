"""The contract every extractor adapter satisfies (spec 04 acceptance)."""
from ext_helpers import make_doc, unit
from weave_core.stages import UnitKind


def run_contract(factory, docs):
    """factory(doc) -> adapter whose documents are served from `docs`; returns each doc's result."""
    results = []
    for doc in docs + [make_doc("", doc_id="fake:empty")]:
        adapter = factory(doc)
        result = adapter.process(unit(doc), {})
        mentions = result.output["mentions"]
        assert [m["text"] for m in mentions] == [doc.text[m["start"]:m["end"]] for m in mentions]
        spans = [(m["start"], m["end"]) for m in mentions]
        assert spans == sorted(spans)
        ids = [e.id for e in result.emits]
        assert len(ids) == len(set(ids)) == len(mentions)
        assert all(e.kind == UnitKind.mention for e in result.emits)
        assert [r["id"] for r in result.rows["mentions"]] == ids
        if not doc.text:
            assert mentions == [] and result.emits == []
        results.append(result)
    return results
