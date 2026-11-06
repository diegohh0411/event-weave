from datetime import datetime, timezone

import pytest

from weave_enrichers.timenorm import check_normalized

PUBLISHED = datetime(2024, 5, 2, 9, 0, tzinfo=timezone.utc)


def test_valid_date_accepted():
    assert check_normalized("2024-05-01", PUBLISHED) == "2024-05-01"


@pytest.mark.parametrize("value", [
    "2024-05-01T10:30",
    "2024-05-01T10:30:15",
    "2024-05-01T10:30Z",
    "2024-05-01T10:30:15Z",
    "2024-05-01T10:30+02:00",
    "2024-05-01T10:30:15-05:30",
])
def test_valid_datetime_accepted(value):
    assert check_normalized(value, PUBLISHED) == value


@pytest.mark.parametrize("value", [
    "2024-05-01/2024-05-03",
    "2024-05-01T10:00/2024-05-01T12:00Z",
    "2024-05-01/2024-05-01T12:00+01:00",
])
def test_valid_interval_accepted(value):
    assert check_normalized(value, PUBLISHED) == value


def test_relative_text_is_none():
    assert check_normalized("last Monday", PUBLISHED) is None


def test_relative_text_without_publication_date_is_none():
    assert check_normalized("2024-05-01", None, "last Monday") is None
    assert check_normalized("2024-05-01T10:00", None, "tomorrow at 10am") is None


def test_relative_text_with_publication_date_is_accepted():
    assert check_normalized("2024-05-01", PUBLISHED, "last Monday") == "2024-05-01"


def test_absolute_text_without_publication_date_is_accepted():
    assert check_normalized("2024-05-01", None, "1 May 2024") == "2024-05-01"


@pytest.mark.parametrize("value", [
    "2024-13-01",
    "2024-02-30",
    "2024-05-01T25:00",
    "2024-05-01T10:60",
    "2024-05-01T10:30+25:00",
    "2024-05-01/2024-13-01",
])
def test_impossible_dates_are_none(value):
    assert check_normalized(value, PUBLISHED) is None


@pytest.mark.parametrize("value", [
    None,
    "",
    "2024-5-1",
    "20240501",
    "2024-05-01 10:00",
    "2024-05-01T10:00:00.123",
    "2024-05-01T10",
    "2024-05-01/",
    "/2024-05-01",
    "2024-05-01/2024-05-02/2024-05-03",
    "2024-05-01/last Monday",
    " 2024-05-01",
])
def test_malformed_values_are_none(value):
    assert check_normalized(value, PUBLISHED) is None
