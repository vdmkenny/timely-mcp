import pytest

import main as timely


@pytest.mark.parametrize(
    "value",
    ["2026-01-01", "2026-12-31"],
)
def test_parse_date_valid(value):
    assert timely._parse_date(value).isoformat() == value


@pytest.mark.parametrize("value", ["01/01/2026", "2026-1-1", "not-a-date", ""])
def test_parse_date_invalid(value):
    with pytest.raises(timely.ApiError):
        timely._parse_date(value)


def test_date_range_reversed():
    with pytest.raises(timely.ApiError):
        timely._date_range("2026-01-10", "2026-01-01")


def test_date_range_exceeds_max():
    with pytest.raises(timely.ApiError):
        timely._date_range("2026-01-01", "2027-01-01", max_days=100)


def test_duration_minutes_cap():
    with pytest.raises(timely.ApiError):
        timely._duration_fields(1441, None, None)


def test_duration_mutually_exclusive():
    with pytest.raises(timely.ApiError):
        timely._duration_fields(60, "09:00", "10:00")


def test_duration_clock_ordering():
    with pytest.raises(timely.ApiError):
        timely._duration_fields(None, "17:00", "09:00")


def test_duration_clock_ok():
    assert timely._duration_fields(None, "09:00", "17:00")["from"] == "09:00"


def test_duration_half_legacy_encoding_rejected():
    with pytest.raises(timely.ApiError):
        timely._duration_fields(None, "hours:3", None)


def test_parse_label_ids_comma_string():
    assert timely._parse_label_ids("2531527,2531529") == [2531527, 2531529]


def test_parse_label_ids_bad_input():
    with pytest.raises(timely.ApiError):
        timely._parse_label_ids("abc,123")


def test_fingerprint_ignores_key_order_and_label_order():
    a = timely.EventSpec(day="2026-08-06", duration_minutes=90, note="x", project_id=7, user_id=3, label_ids=[2, 1])
    b = timely.EventSpec(day="2026-08-06", duration_minutes=90, note="  x  ", project_id=7, user_id=3, label_ids=[1, 2])
    assert timely._event_fingerprint(a) == timely._event_fingerprint(b)
