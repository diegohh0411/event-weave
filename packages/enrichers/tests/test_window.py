from datetime import datetime, timezone

from helpers import make_doc, make_mention
from weave_enrichers.window import build_window


def test_window_marks_by_offset():
    doc = make_doc("He said he said no.")
    second = doc.text.rindex("said")
    w = build_window(doc, make_mention(doc, second, second + 4), 1)
    assert w.text == "He said he ⟦said⟧ no." and w.trigger == (second, second + 4) and w.offset == 0


def test_window_spans_neighbour_sentences():
    doc = make_doc("One came. Two went. Three stayed. Four left.")
    start = doc.text.index("went")
    w = build_window(doc, make_mention(doc, start, start + 4, sentence=1), 1)
    assert w.text == "One came. Two ⟦went⟧. Three stayed." and w.offset == 0
    w0 = build_window(doc, make_mention(doc, start, start + 4, sentence=1), 0)
    assert w0.text == "Two ⟦went⟧." and w0.offset == doc.text.index("Two")


def test_window_header_without_date():
    doc = make_doc("It rained.", title="Storm", outlet="Daily")
    w = build_window(doc, make_mention(doc, 3, 8), 1)
    assert w.header == "Title: Storm\nOutlet: Daily"


def test_window_header_full_and_empty():
    when = datetime(2024, 5, 1, tzinfo=timezone.utc)
    doc = make_doc("It rained.", title="Storm", outlet="Daily", published_at=when)
    assert build_window(doc, make_mention(doc, 3, 8), 1).header.endswith("Published: 2024-05-01T00:00:00+00:00")
    assert build_window(make_doc("x", title=""), make_mention(make_doc("x"), 0, 1), 1).header == ""
