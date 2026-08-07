# Timely MCP Server

An [MCP](https://modelcontextprotocol.io) server for [Timely](https://www.timelyapp.com)
time tracking via the web app's internal cookie-authenticated API. Tailored
for safe, repeatable timesheet backfills.

This fork uses **cookie-based authentication** instead of Nango OAuth.

## Features

- Full CRUD for accounts, clients, projects, users, teams, labels, forecasts.
- Compact event listing with bounded, calendar-aware daily/hour reporting.
- Dry-run backfill planning and idempotent bulk creation with rollback.
- Name-based lookups for projects and labels.
- Read-only and allowed-account safety switches.

## Requirements

- Python 3.13+
- A Timely login and a browser to copy the `_memory_session` cookie.
- Managed by [uv](https://docs.astral.sh/uv/).

## Setup

### 1. Get your Timely session cookie

1. Open your browser and log in to https://app.timelyapp.com
2. Open Developer Tools (`F12` / `Cmd+Option+I`).
3. Go to the **Application** tab (Chrome/Edge) or **Storage** tab (Firefox).
4. Expand **Cookies** → `app.timelyapp.com`.
5. Copy the value of the `_memory_session` cookie (not its name).

> The cookie expires periodically (typically after a few weeks), after which the
> server returns a clear `Unauthorized` error and you must refresh it.

### 2. Configure your MCP client

For **OpenCode**, add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "timely": {
      "type": "local",
      "command": [
        "uv",
        "run",
        "--directory",
        "/path/to/timely-mcp",
        "timely-mcp"
      ],
      "enabled": true,
      "environment": {
        "TIMELY_SESSION_COOKIE": "YOUR_COOKIE_HERE"
      }
    }
  }
}
```

For **GitHub Copilot**, add a similar entry to its MCP settings.

### 3. Run the server

```bash
uv run timely-mcp
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `TIMELY_SESSION_COOKIE` | Required. `_memory_session` cookie value. |
| `TIMELY_READ_ONLY` | Set to `1`/`true` to reject every write operation. |
| `TIMELY_ALLOWED_ACCOUNT_IDS` | Optional comma-separated whitelist of accounts allowed for writes. |

A local `.env` file is supported but never overrides environment variables
provided by the MCP client.

## Safety switches

The server supports dry-run by default for bulk operations, optional read-only
mode (`TIMELY_READ_ONLY`), and an allowed-account allowlist. Bulk creation:

- Prefetches and fingerprints existing entries.
- Rejects or skips exact duplicates depending on policy.
- Enforces a per-day minute cap across all users in the batch.
- Marks ambiguous write timeouts for reconciliation instead of blind retries.
- Rolls back entries created by a failed batch when `rollback_on_failure`.

Plans returned by `plan_timesheet_backfill` carry a hash; `apply_timesheet_plan`
recomputes it against live data and refuses to apply if entries changed.

## Tool inventory

Backfill & analysis

- `get_daily_hours` – calendar-complete per-day totals with configurable
  workdays and excluded dates; zero-entry workdays remain visible.
- `list_events` – compact entries with bounded ranges and result limits.
- `plan_timesheet_backfill` – dry-run plan with per-day gaps and a plan hash.
- `apply_timesheet_plan` – apply a stored plan after revalidating live data.
- `bulk_create_events` – validated bulk creation (dry-run by default).
- `find_projects`, `find_labels` – bounded name-based lookups.

Standard CRUD

- Accounts, clients, projects, users, teams, labels, forecasts.
- Events: `create_event`, `get_event`, `update_event`, `delete_event`,
  `start_timer`, `stop_timer`.

Reports & access

- `get_reports`, `get_permissions`, `list_roles`, `get_user_capacities`.

## Notes

- Webhook endpoints and Nango OAuth were removed: the cookie-based API does not
  support them.
- `delete_client` is intentionally absent; removing clients is destructive and
  not exposed by the internal web API used here.

## Development

```bash
uv sync --group dev
uv run pytest
```
