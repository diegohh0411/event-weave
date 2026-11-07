import pytest
import yaml

from weave_eval.paper import DEFAULT_PATH, load_paper


def test_paper_file_cites_source():
    paper = load_paper()
    assert paper.source["url"] == "https://aclanthology.org/2024.findings-naacl.245.pdf"
    assert paper.source["anthology_id"] == "2024.findings-naacl.245"
    assert [t.table for t in paper.tables] == [4, 5, 6, 7]
    assert all(isinstance(t.page, int) and t.page > 0 for t in paper.tables)
    en = paper.annotation_stats["en"]
    assert (en["chains"], en["non_singleton"], en["mentions"]) == (20040, 1280, 22445)


def test_values_in_range():
    paper = load_paper()
    for t in paper.tables:
        for row in t.rows:
            for metric in ("muc", "b3", "ceafe", "blanc", "conll"):
                cell = row[metric]
                for v in (cell or {}).values() if cell else []:
                    assert v is None or 0 <= v <= 100


def _write(tmp_path, mutate):
    data = yaml.safe_load(DEFAULT_PATH.read_text())
    mutate(data)
    p = tmp_path / "paper.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_out_of_range_value_rejected(tmp_path):
    def bad(d):
        d["tables"][0]["rows"][0]["muc"]["f1"] = 101.5
    with pytest.raises(ValueError, match="0-100"):
        load_paper(_write(tmp_path, bad))


def test_missing_page_rejected(tmp_path):
    def bad(d):
        del d["tables"][1]["page"]
    with pytest.raises(ValueError, match="page"):
        load_paper(_write(tmp_path, bad))


def test_unchecked_file_flagged(tmp_path):
    # the render assertion (output prints UNCHECKED) is added in Task 8, when `render` exists
    assert load_paper().checked is False  # shipped with one name in checked_by

    def two(d):
        d["checked_by"] = ["A", "B"]
    assert load_paper(_write(tmp_path, two)).checked is True

    def same(d):
        d["checked_by"] = ["A", "A"]
    assert load_paper(_write(tmp_path, same)).checked is False
