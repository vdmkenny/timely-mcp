import pytest

import main as timely


def _event(day, hours=0, minutes=0, seconds=0, user_id=1, project_id=1, label_ids=None):
    return {
        "day": day,
        "user_id": user_id,
        "project_id": project_id,
        "note": "work",
        "duration": {"hours": hours, "minutes": minutes, "seconds": seconds},
        "label_ids": label_ids or [],
    }


def test_event_minutes_rounds_seconds_up():
    assert timely._event_minutes(_event("2026-08-06", minutes=29, seconds=30)) == 30
    assert timely._event_minutes(_event("2026-08-06", minutes=29, seconds=29)) == 29


def test_fetch_events_after_invoke(monkeypatch):
    recorded = {}

    def fake_make_request(method, endpoint, data=None, params=None, account_id=None):
        recorded["user_id"] = params.get("user_id")
        return [_event("2026-08-06", user_id=params.get("user_id"))]

    monkeypatch.setattr(timely, "make_request", fake_make_request)
    events = timely._fetch_events_raw(account_id=1, since="2026-08-01", upto="2026-08-07", user_id=42)
    assert recorded["user_id"] == 42
    assert len(events) == 1


def test_fetch_events_only_keeps_target_user(monkeypatch):
    def fake_make_request(method, endpoint, data=None, params=None, account_id=None):
        return [
            _event("2026-08-06", user_id=1),
            _event("2026-08-06", user_id=2),
        ]

    monkeypatch.setattr(timely, "make_request", fake_make_request)
    events = timely._fetch_events_raw(account_id=1, since="2026-08-01", upto="2026-08-07", user_id=1)
    assert len(events) == 1


def test_resolve_user_calls_current(monkeypatch):
    monkeypatch.setattr(
        timely,
        "make_request",
        lambda *a, **k: {"id": 7},
    )
    assert timely._resolve_user_id(1, None) == 7


def test_resolve_user_explicit():
    assert timely._resolve_user_id(1, 9) == 9


def test_workdays_validation():
    with pytest.raises(timely.ApiError):
        timely._parse_workdays("MO,XX")
    assert timely._parse_workdays("MO,tu,FR") == {"MO", "TU", "FR"}


def test_excluded_dates_validation():
    with pytest.raises(timely.ApiError):
        timely._parse_excluded_dates("2026-1-1")
    assert timely._parse_excluded_dates("2026-01-01, 2026-01-02") == {"2026-01-01", "2026-01-02"}
