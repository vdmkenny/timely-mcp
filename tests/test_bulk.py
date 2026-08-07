import pytest

import main as timely

ACCOUNT_ID = 1
USER_ID = 10


def _spec(day="2026-08-06", minutes=60, project_id=5, note="x", label_ids=None):
    return timely.EventSpec(
        day=day,
        duration_minutes=minutes,
        project_id=project_id,
        user_id=USER_ID,
        note=note,
        label_ids=label_ids or [],
    )


class FakeTimely:
    """Simulates the Timely /hours endpoints for one user."""

    def __init__(self):
        self.next_event_id = 1000
        self.delete_calls = []
        self.create_payloads = []
        self.fail_creates = None
        self.return_existing = []

    def request(self, method, endpoint, data=None, params=None, account_id=None):
        if method == "POST" and endpoint.endswith("/hours"):
            self.create_payloads.append(data)
            if self.fail_creates is not None:
                self.fail_creates -= 1
                if self.fail_creates < 0:
                    raise timely.ApiError("boom")
                self.fail_creates += 1
                raise timely.ApiError("boom", ambiguous_write=False)
            self.next_event_id += 1
            return {
                "id": self.next_event_id,
                "day": data["event"]["day"],
                "user_id": data["event"]["user_id"],
                "project_id": data["event"]["project_id"],
                "note": data["event"]["note"],
                "duration": {
                    "hours": data["event"]["hours"],
                    "minutes": data["event"]["minutes"],
                    "seconds": 0,
                },
                "label_ids": data["event"]["label_ids"],
            }
        if method == "DELETE" and "/hours/" in endpoint:
            self.delete_calls.append(int(endpoint.rsplit("/", 1)[1]))
            return {}
        if method == "GET" and endpoint.endswith("/hours"):
            events = list(self.return_existing)
            since = params.get("since")
            upto = params.get("upto")
            events = [e for e in events if since <= e["day"] <= upto]
            return events
        if method == "GET" and endpoint.endswith("/users/current"):
            return {"id": USER_ID}
        raise AssertionError(f"Unexpected call {method} {endpoint}")


@pytest.fixture
def fake(monkeypatch):
    transport = FakeTimely()
    monkeypatch.setattr(timely, "make_request", transport.request)
    return transport


def _run_bulk(fake, **overrides):
    kwargs = {
        "account_id": ACCOUNT_ID,
        "entries": [_spec()],
        "default_user_id": USER_ID,
        "dry_run": True,
        "max_daily_minutes": 480,
        "duplicate_policy": "skip_exact",
        "rollback_on_failure": True,
    }
    kwargs.update(overrides)
    return timely._execute_bulk_events(**kwargs)


def test_dry_run_performs_no_writes(fake):
    report = _run_bulk(fake, dry_run=True)
    assert report.dry_run and report.success
    assert report.created_count == 0
    assert fake.create_payloads == []
    assert report.results[0].status == "planned"


def test_create_apply(fake):
    report = _run_bulk(fake, dry_run=False)
    assert report.success
    assert report.created_count == 1
    assert len(fake.create_payloads) == 1
    assert report.results[0].status == "created"


def test_create_hits_daily_cap(fake, monkeypatch):
    existing = {
        "id": 1,
        "day": "2026-08-06",
        "user_id": USER_ID,
        "project_id": 99,
        "note": "other",
        "duration": {"hours": 7, "minutes": 30, "seconds": 0},
        "label_ids": [],
    }
    monkeypatch.setattr(timely, "_fetch_events_raw", lambda **kw: [existing])
    with pytest.raises(timely.ApiError):
        _run_bulk(fake, dry_run=True)


def test_exact_duplicate_skipped(fake, monkeypatch):
    existing = {
        "id": 2,
        "day": "2026-08-06",
        "user_id": USER_ID,
        "project_id": 5,
        "note": "x",
        "duration": {"hours": 1, "minutes": 0, "seconds": 0},
        "label_ids": [],
    }
    fake.return_existing = [existing]
    monkeypatch.setattr(timely, "_fetch_events_raw", lambda **kw: [existing])
    report = _run_bulk(fake, dry_run=True)
    assert report.results[0].status == "skipped"


def test_failure_rolls_back_created(fake, monkeypatch):
    calls = {"count": 0}

    class Boom(Exception):
        pass

    def fake_make_request(method, endpoint, data=None, params=None, account_id=None):
        if method == "POST":
            calls["count"] += 1
            if calls["count"] == 2:
                raise timely.ApiError("boom")
            return {
                "id": 100 + calls["count"],
                "day": data["event"]["day"],
                "user_id": data["event"]["user_id"],
                "project_id": data["event"]["project_id"],
                "note": data["event"]["note"],
                "duration": {"hours": 1, "minutes": 0, "seconds": 0},
                "label_ids": [],
            }
        if method == "DELETE":
            fake.delete_calls.append(int(endpoint.rsplit("/", 1)[1]))
            return {}
        if method == "GET":
            return []
        raise Boom(method)

    monkeypatch.setattr(timely, "make_request", fake_make_request)
    monkeypatch.setattr(timely, "_fetch_events_raw", lambda **kw: [])
    report = timely._execute_bulk_events(
        account_id=ACCOUNT_ID,
        entries=[_spec(day="2026-08-04"), _spec(day="2026-08-05")],
        default_user_id=USER_ID,
        dry_run=False,
        max_daily_minutes=480,
        duplicate_policy="skip_exact",
        rollback_on_failure=True,
    )
    assert not report.success
    assert report.rolled_back_count == 1
    rolled_back_ids = [r.event_id for r in report.results if r.status == "rolled_back"]
    assert rolled_back_ids == [101]


def test_missing_user_resolution(monkeypatch):
    monkeypatch.setattr(timely, "_fetch_events_raw", lambda **kw: [])
    with pytest.raises(timely.ApiError):
        timely._bulk_preflight(
            account_id=ACCOUNT_ID,
            entries=[timely.EventSpec(day="2026-08-06", duration_minutes=60)],
            default_user_id=None,
            max_daily_minutes=480,
            duplicate_policy="skip_exact",
        )


def test_plan_then_apply_round_trip(monkeypatch):
    state = {"events": []}

    class FakePlanTimely:
        def request(self, method, endpoint, data=None, params=None, account_id=None):
            if method == "GET" and endpoint.endswith("/hours"):
                return list(state["events"])
            if method == "GET" and endpoint.endswith("/users/current"):
                return {"id": USER_ID}
            if method == "POST" and endpoint.endswith("/hours"):
                entry = {"id": len(state["events"]) + 1, **data["event"]}
                state["events"].append(entry)
                return entry
            raise AssertionError(f"Unexpected {method} {endpoint}")

    monkeypatch.setattr(timely, "make_request", FakePlanTimely().request)
    plan = timely.plan_timesheet_backfill(
        account_id=ACCOUNT_ID,
        since="2026-08-03",
        upto="2026-08-07",
        entries=[_spec(day="2026-08-06")],
        user_id=USER_ID,
        target_minutes=480,
        filler_project_id=5,
        filler_note="devops",
    )
    assert plan.events
    report = timely.apply_timesheet_plan(plan.plan_hash)
    assert report.success
    assert report.created_count == len(plan.events)
    assert len(state["events"]) == len(plan.events)


def test_read_only_blocks_apply_mode_before_writes(monkeypatch):
    called = {"writes": 0}

    def fake_make_request(method, endpoint, data=None, params=None, account_id=None):
        if method != "GET":
            called["writes"] += 1
        return []

    monkeypatch.setattr(timely, "make_request", fake_make_request)
    monkeypatch.setenv(timely.READ_ONLY_ENV_VAR, "1")
    with pytest.raises(timely.ApiError):
        timely._ensure_write_allowed(ACCOUNT_ID)
    with pytest.raises(RuntimeError, match="disabled"):
        timely.bulk_create_events(
            account_id=ACCOUNT_ID,
            entries=[_spec(day="2026-08-11")],
            default_user_id=USER_ID,
            dry_run=False,
        )
    assert called["writes"] == 0
