#!/usr/bin/env python3
"""Test all read-only MCP tools against the live Timely API."""

import os
import sys
import traceback

# Ensure the cookie is set
if not os.environ.get("TIMELY_SESSION_COOKIE"):
    print("ERROR: Set TIMELY_SESSION_COOKIE env var first")
    sys.exit(1)

from main import (
    list_accounts, get_account,
    list_clients, get_client,
    list_projects, get_project,
    list_users, get_user, get_current_user,
    list_events, get_event,
    list_teams, get_team,
    list_labels, get_label,
    list_forecasts,
    get_reports, get_permissions, list_roles, get_user_capacities,
)

passed = 0
failed = 0
skipped = 0


def run_test(name, fn):
    global passed, failed
    try:
        result = fn()
        print(f"  PASS  {name}")
        passed += 1
        return result
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        traceback.print_exc()
        failed += 1
        return None


def skip_test(name, reason):
    global skipped
    print(f"  SKIP  {name}: {reason}")
    skipped += 1


# ---- Accounts ----
print("\n=== Accounts ===")
accounts_result = run_test("list_accounts()", list_accounts)
account_id = None
if accounts_result and accounts_result.accounts:
    account_id = accounts_result.accounts[0].id
    print(f"         -> Found {len(accounts_result.accounts)} account(s), using account_id={account_id}")
    run_test(f"get_account({account_id})", lambda: get_account(account_id))
else:
    print("  FATAL: No accounts found, cannot continue")
    sys.exit(1)

# ---- Clients ----
print("\n=== Clients ===")
clients_result = run_test(f"list_clients({account_id})", lambda: list_clients(account_id))
if clients_result and clients_result.clients:
    cid = clients_result.clients[0].id
    print(f"         -> Found {len(clients_result.clients)} client(s)")
    run_test(f"get_client({account_id}, {cid})", lambda: get_client(account_id, cid))
else:
    skip_test(f"get_client({account_id}, ?)", "no clients to fetch")

# ---- Projects ----
print("\n=== Projects ===")
projects_result = run_test(f"list_projects({account_id})", lambda: list_projects(account_id))
if projects_result and projects_result.projects:
    pid = projects_result.projects[0].id
    print(f"         -> Found {len(projects_result.projects)} project(s)")
    run_test(f"get_project({account_id}, {pid})", lambda: get_project(account_id, pid))
else:
    skip_test(f"get_project({account_id}, ?)", "no projects to fetch")

# ---- Users ----
print("\n=== Users ===")
users_result = run_test(f"list_users({account_id})", lambda: list_users(account_id))
user_id = None
if users_result and users_result.users:
    user_id = users_result.users[0].id
    print(f"         -> Found {len(users_result.users)} user(s)")
    run_test(f"get_user({account_id}, {user_id})", lambda: get_user(account_id, user_id))
else:
    skip_test(f"get_user({account_id}, ?)", "no users to fetch")

run_test(f"get_current_user({account_id})", lambda: get_current_user(account_id))

# ---- Events ----
print("\n=== Events ===")
# Use a date range to ensure we get events (no-filter returns empty)
events_result = run_test(
    f"list_events({account_id}, since='2026-03-01', upto='2026-04-01')",
    lambda: list_events(account_id, since="2026-03-01", upto="2026-04-01"),
)
if events_result and events_result.events:
    eid = events_result.events[0].id
    print(f"         -> Found {len(events_result.events)} event(s)")
    run_test(f"get_event({account_id}, {eid})", lambda: get_event(account_id, eid))
else:
    skip_test(f"get_event({account_id}, ?)", "no events in date range")

# Also test with a different date range
run_test(
    f"list_events({account_id}, since='2025-01-01', upto='2025-12-31')",
    lambda: list_events(account_id, since="2025-01-01", upto="2025-12-31"),
)

# ---- Teams ----
print("\n=== Teams ===")
teams_result = run_test(f"list_teams({account_id})", lambda: list_teams(account_id))
if teams_result and teams_result.teams:
    tid = teams_result.teams[0].id
    print(f"         -> Found {len(teams_result.teams)} team(s)")
    run_test(f"get_team({account_id}, {tid})", lambda: get_team(account_id, tid))
else:
    skip_test(f"get_team({account_id}, ?)", "no teams to fetch")

# ---- Labels ----
print("\n=== Labels ===")
labels_result = run_test(f"list_labels({account_id})", lambda: list_labels(account_id))
if labels_result and labels_result.labels:
    lid = labels_result.labels[0].id
    print(f"         -> Found {len(labels_result.labels)} label(s)")
    run_test(f"get_label({account_id}, {lid})", lambda: get_label(account_id, lid))
else:
    skip_test(f"get_label({account_id}, ?)", "no labels to fetch")

# ---- Forecasts ----
print("\n=== Forecasts ===")
run_test(f"list_forecasts({account_id})", lambda: list_forecasts(account_id))

# ---- Reports & Utility ----
print("\n=== Reports & Utility ===")
run_test(f"get_reports({account_id})", lambda: get_reports(account_id))
if user_id:
    run_test(f"get_permissions({account_id}, {user_id})", lambda: get_permissions(account_id, user_id))
    run_test(f"get_user_capacities({account_id}, {user_id})", lambda: get_user_capacities(account_id, user_id))
else:
    skip_test("get_permissions", "no user_id available")
    skip_test("get_user_capacities", "no user_id available")
run_test(f"list_roles({account_id})", lambda: list_roles(account_id))

# ---- Summary ----
print(f"\n{'='*40}")
print(f"RESULTS: {passed} passed, {failed} failed, {skipped} skipped")
print(f"{'='*40}")
sys.exit(1 if failed else 0)
