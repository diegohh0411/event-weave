from weave_enrichers.verify import locate_nearest, verify_field


def test_locate_nearest_prefers_closest():
    text = "police arrived. They said it. Then police left."
    anchor = text.index("said")
    assert locate_nearest(text, "police", anchor) == (text.rindex("police"), text.rindex("police") + 6)
    assert locate_nearest(text, "police", 0) == (0, 6)


def test_locate_nearest_tie_goes_earlier():
    assert locate_nearest("a b a", "a", 2) == (0, 1)


def test_locate_nearest_case_sensitive():
    assert locate_nearest("Police came", "police", 0) is None


def test_locate_nearest_blank_or_absent():
    assert locate_nearest("abc", "", 0) is None
    assert locate_nearest("a b", " ", 0) is None
    assert locate_nearest("abc", "zzz", 0) is None


def test_verify_participants_multi():
    text = "a met b today"
    field, discarded = verify_field(text, ["a", "b", "c"], 2, multi=True)
    assert field.spans == [(0, 1), (6, 7)] and field.text == "a; b" and discarded == 1


def test_verify_dedups_spans():
    field, discarded = verify_field("x y", ["x", "x"], 0, multi=True)
    assert field.spans == [(0, 1)] and discarded == 0


def test_verify_single_keeps_best_located():
    text = "aa bb cc"
    field, discarded = verify_field(text, ["aa", "cc", "zz"], 6, multi=False)
    assert field.spans == [(6, 8)] and field.text == "cc" and discarded == 1


def test_verify_empty():
    field, discarded = verify_field("abc", [], 0, multi=True)
    assert field.text is None and field.spans == [] and discarded == 0
