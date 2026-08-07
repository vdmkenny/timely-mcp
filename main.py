#!/usr/bin/env python3
"""
Timely API MCP Server
A Model Context Protocol server for interacting with the Timely time tracking API.
"""

from collections import Counter
import hashlib
from http import HTTPStatus
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Union

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from dotenv import load_dotenv
from mcp.server import MCPServer

# Explicit MCP environment values must win over a local .env file.
load_dotenv(override=False)

# Initialize the MCP server
mcp = MCPServer("Timely API")

# Base URL for Timely (using the web app's internal API with session cookie auth)
BASE_URL = "https://app.timelyapp.com"


def get_session_cookie() -> str:
    """Get session cookie from TIMELY_SESSION_COOKIE environment variable"""
    cookie = os.environ.get("TIMELY_SESSION_COOKIE")
    if not cookie:
        raise ApiError("Missing TIMELY_SESSION_COOKIE environment variable")
    return cookie


HTTP_CONNECT_TIMEOUT_SECONDS = 5
HTTP_READ_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT_SECONDS, HTTP_READ_TIMEOUT_SECONDS)
HTTP_RETRY_COUNT = 3
HTTP_RETRY_BACKOFF_SECONDS = 0.5
HTTP_RETRY_STATUS_CODES = (
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
)
HTTP_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MINUTES_PER_HOUR = 60
SECONDS_PER_MINUTE = 60
ROUND_UP_SECONDS_THRESHOLD = SECONDS_PER_MINUTE // 2
MAX_EVENT_DURATION_MINUTES = 24 * MINUTES_PER_HOUR
DEFAULT_DAILY_TARGET_MINUTES = 8 * MINUTES_PER_HOUR
DEFAULT_EVENT_LOOKBACK_DAYS = 30
MAX_EVENT_RANGE_DAYS = 366
MAX_EVENT_RESULTS = 500
MAX_PROJECT_FETCH_RESULTS = 500
MAX_BULK_EVENTS = 100
MAX_CACHED_BACKFILL_PLANS = 20
MAX_LOOKUP_RESULTS = 100
DEFAULT_EVENT_RESULTS = 100
DEFAULT_LOOKUP_RESULTS = 20
DEFAULT_WORKDAYS = "MO,TU,WE,TH,FR"
WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
READ_ONLY_ENV_VAR = "TIMELY_READ_ONLY"
ALLOWED_ACCOUNTS_ENV_VAR = "TIMELY_ALLOWED_ACCOUNT_IDS"

_http = requests.Session()
_http.headers.update({"User-Agent": "timely-mcp/0.2"})
_http.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=HTTP_RETRY_COUNT,
            backoff_factor=HTTP_RETRY_BACKOFF_SECONDS,
            status_forcelist=HTTP_RETRY_STATUS_CODES,
            allowed_methods=HTTP_RETRY_METHODS,
            respect_retry_after_header=True,
        )
    ),
)

_csrf_token_cache: Dict[tuple[int, str], str] = {}


def _cookie_cache_key(account_id: int, cookie: str) -> tuple[int, str]:
    return account_id, hashlib.sha256(cookie.encode()).hexdigest()

def get_csrf_token(account_id: int) -> str:
    """Fetch and cache the CSRF token from the Timely web app."""
    session_cookie = get_session_cookie()
    cache_key = _cookie_cache_key(account_id, session_cookie)
    if cache_key in _csrf_token_cache:
        return _csrf_token_cache[cache_key]
    resp = _http.get(
        f"{BASE_URL}/{account_id}",
        headers={"Cookie": f"_memory_session={session_cookie}"},
        allow_redirects=True,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code == HTTPStatus.UNAUTHORIZED:
        raise ApiError("Unauthorized: session cookie is invalid or expired. Refresh TIMELY_SESSION_COOKIE.")
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ApiError(f"Failed to fetch CSRF token: {exc}") from exc
    m = re.search(r"<meta[^>]+name=['\"]csrf-token['\"][^>]+content=['\"]([^'\"]+)['\"]", resp.text)
    if not m:
        m = re.search(r"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+name=['\"]csrf-token['\"]", resp.text)
    if not m:
        raise ApiError("Could not find CSRF token in Timely page")
    _csrf_token_cache[cache_key] = m.group(1)
    return _csrf_token_cache[cache_key]


class ApiError(Exception):
    """Actionable Timely API or input error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        ambiguous_write: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.ambiguous_write = ambiguous_write


def _tool_error(action: str, exc: Exception) -> RuntimeError:
    return RuntimeError(f"Failed to {action}: {exc}")


def _ensure_write_allowed(account_id: int) -> None:
    if os.environ.get(READ_ONLY_ENV_VAR, "").lower() in {"1", "true", "yes"}:
        raise ApiError(f"Writes are disabled by {READ_ONLY_ENV_VAR}")
    allowed = os.environ.get(ALLOWED_ACCOUNTS_ENV_VAR)
    if not allowed:
        return
    try:
        allowed_ids = {int(value.strip()) for value in allowed.split(",") if value.strip()}
    except ValueError as exc:
        raise ApiError(f"{ALLOWED_ACCOUNTS_ENV_VAR} must contain comma-separated integer IDs") from exc
    if account_id not in allowed_ids:
        raise ApiError(f"Writes to account {account_id} are not allowed by {ALLOWED_ACCOUNTS_ENV_VAR}")


def _parse_date(value: str, field_name: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(f"{field_name} must use YYYY-MM-DD format: {value!r}") from exc


def _date_range(since: str, upto: str, max_days: int = MAX_EVENT_RANGE_DAYS) -> List[date]:
    start = _parse_date(since, "since")
    end = _parse_date(upto, "upto")
    if end < start:
        raise ApiError("upto must be on or after since")
    count = (end - start).days + 1
    if count > max_days:
        raise ApiError(f"Date range cannot exceed {max_days} days")
    return [start + timedelta(days=offset) for offset in range(count)]


def _validate_time(value: str, field_name: str) -> str:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ApiError(f"{field_name} must use 24-hour HH:MM format: {value!r}") from exc
    return value


def _parse_label_ids(label_ids: Optional[Union[str, List[int]]]) -> List[int]:
    if label_ids is None:
        return []
    if isinstance(label_ids, list):
        values = label_ids
    else:
        try:
            values = [int(value.strip()) for value in label_ids.split(",") if value.strip()]
        except ValueError as exc:
            raise ApiError("label_ids must be comma-separated integer IDs") from exc
    if any(value <= 0 for value in values):
        raise ApiError("label_ids must contain positive integers")
    return list(dict.fromkeys(values))


def _duration_fields(
    duration_minutes: Optional[int],
    from_time: Optional[str],
    to_time: Optional[str],
) -> Dict[str, Any]:
    if duration_minutes is not None:
        if from_time is not None or to_time is not None:
            raise ApiError("Use duration_minutes or from_time/to_time, not both")
        if not 1 <= duration_minutes <= MAX_EVENT_DURATION_MINUTES:
            raise ApiError(
                f"duration_minutes must be between 1 and {MAX_EVENT_DURATION_MINUTES}"
            )
        return {
            "hours": duration_minutes // MINUTES_PER_HOUR,
            "minutes": duration_minutes % MINUTES_PER_HOUR,
            "seconds": 0,
            "from": None,
            "to": None,
        }

    if from_time is None or to_time is None:
        raise ApiError("Provide duration_minutes or both from_time and to_time")
    if from_time.startswith("hours:") or to_time.startswith("minutes:"):
        if not (from_time.startswith("hours:") and to_time.startswith("minutes:")):
            raise ApiError("Legacy duration encoding requires hours:HH and minutes:MM together")
        try:
            hours = int(from_time.removeprefix("hours:"))
            minutes = int(to_time.removeprefix("minutes:"))
        except ValueError as exc:
            raise ApiError("Legacy duration values must be integers") from exc
        total = hours * MINUTES_PER_HOUR + minutes
        if (
            hours < 0
            or not 0 <= minutes < MINUTES_PER_HOUR
            or not 1 <= total <= MAX_EVENT_DURATION_MINUTES
        ):
            raise ApiError("Legacy duration must be between 1 minute and 24 hours")
        return {"hours": hours, "minutes": minutes, "seconds": 0, "from": None, "to": None}

    start = _validate_time(from_time, "from_time")
    end = _validate_time(to_time, "to_time")
    if end <= start:
        raise ApiError("to_time must be later than from_time")
    return {"hours": 0, "minutes": 0, "seconds": 0, "from": start, "to": end}


def _unwrap_list(response: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and isinstance(response.get(key), list):
        return response[key]
    raise ApiError(f"Expected a list response for {key}")


def _send(method: str, url: str, headers: Dict[str, str], data: Optional[Dict], params: Optional[Dict]):
    """Dispatch a single HTTP request."""
    m = method.upper()
    if m == "GET":
        return _http.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
    elif m == "POST":
        return _http.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT)
    elif m == "PUT":
        return _http.put(url, headers=headers, json=data, timeout=HTTP_TIMEOUT)
    elif m == "DELETE":
        return _http.delete(url, headers=headers, timeout=HTTP_TIMEOUT)
    raise ApiError(f"Unsupported HTTP method: {method}")


def _endpoint_account_id(endpoint: str) -> Optional[int]:
    match = re.match(r"^/(\d+)(?:/|$)", endpoint)
    return int(match.group(1)) if match else None


def _csrf_rejected(response: requests.Response) -> bool:
    if response.status_code == HTTPStatus.FORBIDDEN:
        return True
    if response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY:
        return False
    text = response.text.lower()
    return "csrf" in text or "authenticity token" in text


def make_request(method: str, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None, account_id: Optional[int] = None) -> Any:
    """Make HTTP request to Timely API with error handling.

    For write methods (POST/PUT/DELETE) the CSRF token is attached. If the
    server rejects the write with 403/422 (commonly a stale CSRF token), the
    token cache is invalidated and the request is retried once.
    """
    try:
        session_cookie = get_session_cookie()
    except ApiError as e:
        raise ApiError(f"Authentication failed: {str(e)}")

    url = f"{BASE_URL}{endpoint}"
    is_write = method.upper() in ("POST", "PUT", "DELETE")
    resolved_account_id = account_id or _endpoint_account_id(endpoint)
    if is_write and resolved_account_id is None:
        raise ApiError("Write requests require an account-scoped endpoint")
    if is_write:
        _ensure_write_allowed(resolved_account_id)

    def build_headers() -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": f"_memory_session={session_cookie}",
        }
        if is_write and resolved_account_id is not None:
            h["X-CSRF-Token"] = get_csrf_token(resolved_account_id)
        return h

    attempts = 2 if is_write else 1
    try:
        for attempt in range(attempts):
            response = _send(method, url, build_headers(), data, params)

            if is_write and attempt == 0 and _csrf_rejected(response):
                cache_key = _cookie_cache_key(resolved_account_id, session_cookie)
                _csrf_token_cache.pop(cache_key, None)
                continue

            # Handle HTTP errors
            if response.status_code == HTTPStatus.UNAUTHORIZED:
                raise ApiError(
                    "Unauthorized: session cookie is invalid or expired. "
                    "Refresh TIMELY_SESSION_COOKIE.",
                    status_code=response.status_code,
                )
            elif response.status_code == HTTPStatus.FORBIDDEN:
                raise ApiError(
                    "Forbidden: insufficient permissions (or CSRF token rejected)",
                    status_code=response.status_code,
                )
            elif response.status_code == HTTPStatus.NOT_FOUND:
                raise ApiError("Not Found: Resource does not exist", status_code=response.status_code)
            elif response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
                try:
                    error_data = response.json() if response.content else {}
                except requests.exceptions.JSONDecodeError:
                    error_data = response.text
                errors = error_data.get("errors", error_data) if isinstance(error_data, dict) else error_data
                raise ApiError(f"Validation Error: {errors}", status_code=response.status_code)
            elif response.status_code >= HTTPStatus.BAD_REQUEST:
                raise ApiError(
                    f"HTTP {response.status_code}: {response.reason}",
                    status_code=response.status_code,
                )

            # Return JSON response or empty dict for successful requests with no content
            return response.json() if response.content else {}

    except requests.Timeout as e:
        raise ApiError(
            f"Request timed out: {e}",
            ambiguous_write=is_write,
        ) from e
    except requests.RequestException as e:
        raise ApiError(
            f"Request failed: {e}",
            ambiguous_write=is_write,
        ) from e
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        raise ApiError("Invalid JSON response from API")

# ============================================================================
# DATA MODELS FOR STRUCTURED OUTPUT
# ============================================================================

class Account(BaseModel):
    """Account information structure"""
    id: int = Field(description="Account ID")
    name: str = Field(description="Account name")
    created_at: Optional[Union[str, int]] = Field(description="Account creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Account last update timestamp", default=None)


class Client(BaseModel):
    """Client information structure"""
    id: int = Field(description="Client ID")
    name: str = Field(description="Client name")
    active: Optional[bool] = Field(description="Whether client is active", default=None)
    created_at: Optional[Union[str, int]] = Field(description="Client creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Client last update timestamp", default=None)


class Project(BaseModel):
    """Project information structure"""
    id: int = Field(description="Project ID")
    name: str = Field(description="Project name")
    description: Optional[str] = Field(description="Project description", default=None)
    active: Optional[bool] = Field(description="Whether project is active", default=None)
    client_id: Optional[int] = Field(description="Associated client ID", default=None)
    created_at: Optional[Union[str, int]] = Field(description="Project creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Project last update timestamp", default=None)


class User(BaseModel):
    """User information structure"""
    id: int = Field(description="User ID")
    name: str = Field(description="User name")
    email: str = Field(description="User email address")
    user_level: Optional[str] = Field(description="User access level (normal/limited)", default=None)
    active: Optional[bool] = Field(description="Whether user is active", default=None)
    created_at: Optional[Union[str, int]] = Field(description="User creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="User last update timestamp", default=None)


class Event(BaseModel):
    """Event/time entry information structure"""
    model_config = ConfigDict(extra="ignore", validate_by_alias=True, validate_by_name=True)
    id: int = Field(description="Event ID")
    uid: Optional[str] = Field(description="Event unique identifier", default=None)
    project_id: Optional[int] = Field(description="Associated project ID", default=None)
    project_name: Optional[str] = Field(description="Associated project name", default=None)
    user_id: Optional[int] = Field(description="User who logged the time", default=None)
    user_name: Optional[str] = Field(description="User name", default=None)
    day: str = Field(description="Date of the event (YYYY-MM-DD)")
    from_time: Optional[str] = Field(description="Start time (HH:MM)", default=None, alias="from")
    to_time: Optional[str] = Field(description="End time (HH:MM)", default=None, alias="to")
    duration_minutes: int = Field(description="Logged duration in minutes", default=0)
    note: Optional[str] = Field(description="Event description/note", default=None)
    label_ids: List[int] = Field(description="Assigned label IDs", default_factory=list)
    created_at: Optional[Union[str, int]] = Field(description="Event creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Event last update timestamp", default=None)


class Team(BaseModel):
    """Team information structure"""
    id: int = Field(description="Team ID")
    name: str = Field(description="Team name")
    created_at: Optional[Union[str, int]] = Field(description="Team creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Team last update timestamp", default=None)


class Label(BaseModel):
    """Label/tag information structure"""
    id: int = Field(description="Label ID")
    name: str = Field(description="Label name")
    color: Optional[str] = Field(description="Label color", default=None)
    parent_id: Optional[int] = Field(description="Parent label ID (null for top-level)", default=None)
    children: Optional[List["Label"]] = Field(description="Child labels", default=None)
    created_at: Optional[Union[str, int]] = Field(description="Label creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Label last update timestamp", default=None)


class Forecast(BaseModel):
    """Forecast/task information structure"""
    id: int = Field(description="Forecast ID")
    project_id: int = Field(description="Associated project ID")
    user_id: int = Field(description="Assigned user ID")
    day: str = Field(description="Forecast date (YYYY-MM-DD)")
    duration: int = Field(description="Planned duration in minutes")
    note: Optional[str] = Field(description="Forecast description", default=None)
    created_at: Optional[Union[str, int]] = Field(description="Forecast creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Forecast last update timestamp", default=None)


# Response wrapper types
class AccountList(BaseModel):
    """List of accounts"""
    accounts: List[Account] = Field(description="List of account objects")


class ClientList(BaseModel):
    """List of clients"""
    clients: List[Client] = Field(description="List of client objects")


class ProjectList(BaseModel):
    """List of projects"""
    projects: List[Project] = Field(description="List of project objects")


class ProjectMatch(BaseModel):
    id: int = Field(description="Project ID")
    name: str = Field(description="Project name")
    active: Optional[bool] = Field(description="Whether the project is active", default=None)
    client_id: Optional[int] = Field(description="Associated client ID", default=None)


class ProjectMatchList(BaseModel):
    matches: List[ProjectMatch] = Field(description="Matching projects")


class UserList(BaseModel):
    """List of users"""
    users: List[User] = Field(description="List of user objects")


class EventList(BaseModel):
    """List of events"""
    events: List[Event] = Field(description="List of event objects")
    has_more: bool = Field(description="Whether additional events were omitted", default=False)
    since: Optional[str] = Field(description="Inclusive range start", default=None)
    upto: Optional[str] = Field(description="Inclusive range end", default=None)


class TeamList(BaseModel):
    """List of teams"""
    teams: List[Team] = Field(description="List of team objects")


class LabelList(BaseModel):
    """List of labels"""
    labels: List[Label] = Field(description="List of label objects")


class ForecastList(BaseModel):
    """List of forecasts"""
    forecasts: List[Forecast] = Field(description="List of forecast objects")


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(event)
    project = event.get("project")
    user = event.get("user")
    project_id = event.get("project_id")
    user_id = event.get("user_id")
    if isinstance(project_id, dict):
        project = project_id
        project_id = project.get("id")
    if isinstance(user_id, dict):
        user = user_id
        user_id = user.get("id")
    if project_id is None and isinstance(project, dict):
        project_id = project.get("id")
    if user_id is None and isinstance(user, dict):
        user_id = user.get("id")
    normalized.update({
        "project_id": project_id,
        "project_name": project.get("name") if isinstance(project, dict) else None,
        "user_id": user_id,
        "user_name": user.get("name") if isinstance(user, dict) else None,
        "duration_minutes": _event_minutes(event),
        "label_ids": event.get("label_ids") or [],
    })
    return normalized


def _event_model(event: Dict[str, Any]) -> Event:
    return Event.model_validate(_normalize_event(event))


def _event_payload(
    *,
    day: str,
    user_id: Optional[int],
    project_id: Optional[int],
    note: Optional[str],
    label_ids: Optional[Union[str, List[int]]],
    duration_minutes: Optional[int],
    from_time: Optional[str],
    to_time: Optional[str],
) -> Dict[str, Any]:
    _parse_date(day, "day")
    duration = _duration_fields(duration_minutes, from_time, to_time)
    return {
        "event": {
            "day": day,
            "note": note or "",
            "timer_state": "default",
            "timer_started_on": 0,
            "timer_stopped_on": 0,
            "project_id": project_id,
            "forecast_id": None,
            "label_ids": _parse_label_ids(label_ids),
            "user_ids": [],
            "entry_ids": [],
            "timestamps": [],
            "estimated_hours": 0,
            "estimated_minutes": 0,
            "sequence": 1,
            "billable": False,
            "state_id": None,
            "billed": False,
            "locked": False,
            "locked_reason": None,
            "external_links": [],
            "user_id": user_id,
            **duration,
        }
    }


def _create_event_api(
    *,
    account_id: int,
    day: str,
    user_id: Optional[int],
    project_id: Optional[int],
    note: Optional[str],
    label_ids: Optional[Union[str, List[int]]],
    duration_minutes: Optional[int] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
) -> Event:
    payload = _event_payload(
        day=day,
        user_id=user_id,
        project_id=project_id,
        note=note,
        label_ids=label_ids,
        duration_minutes=duration_minutes,
        from_time=from_time,
        to_time=to_time,
    )
    response = make_request("POST", f"/{account_id}/hours", data=payload, account_id=account_id)
    return _event_model(response)


# ============================================================================
# ACCOUNT TOOLS
# ============================================================================

@mcp.tool()
def list_accounts() -> AccountList:
    """List all accounts associated with the authenticated user"""
    try:
        response = make_request("GET", "/accounts")
        # Handle both array response and object response
        if isinstance(response, list):
            accounts_data = response
        else:
            # If it's a dict, look for accounts key or treat as single account
            accounts_data = response.get("accounts", [response] if "id" in response else [])
        
        accounts = []
        for account_data in accounts_data:
            try:
                accounts.append(Account.model_validate(account_data))
            except Exception as e:
                raise ApiError(f"Invalid account response: {e}") from e
        
        return AccountList(accounts=accounts)
    except ApiError as e:
        raise _tool_error("list accounts", e) from e


@mcp.tool()
def get_account(account_id: int) -> Account:
    """Retrieve a specific account by ID"""
    try:
        response = make_request("GET", f"/accounts/{account_id}")
        return Account.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get account {account_id}", e) from e


# ============================================================================
# CLIENT TOOLS
# ============================================================================

@mcp.tool()
def list_clients(account_id: int, limit: Optional[int] = None, offset: Optional[int] = None) -> ClientList:
    """List all clients for an account"""
    try:
        params = {}
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
            
        response = make_request("GET", f"/{account_id}/clients", params=params)
        clients = [Client.model_validate(client) for client in _unwrap_list(response, "clients")]
        return ClientList(clients=clients)
    except ApiError as e:
        raise _tool_error("list clients", e) from e


@mcp.tool()
def get_client(account_id: int, client_id: int) -> Client:
    """Retrieve a specific client by ID"""
    try:
        response = make_request("GET", f"/{account_id}/clients/{client_id}")
        return Client.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get client {client_id}", e) from e


@mcp.tool()
def create_client(account_id: int, name: str, active: bool = True) -> Client:
    """Create a new client"""
    try:
        data = {
            "client": {
                "name": name,
                "active": active
            }
        }
        response = make_request("POST", f"/{account_id}/clients", data=data, account_id=account_id)
        return Client.model_validate(response)
    except ApiError as e:
        raise _tool_error("create client", e) from e


@mcp.tool()
def update_client(account_id: int, client_id: int, name: Optional[str] = None, active: Optional[bool] = None) -> Client:
    """Update an existing client"""
    try:
        data = {"client": {}}
        if name is not None:
            data["client"]["name"] = name
        if active is not None:
            data["client"]["active"] = active
            
        response = make_request("PUT", f"/{account_id}/clients/{client_id}", data=data, account_id=account_id)
        return Client.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update client {client_id}", e) from e


# ============================================================================
# PROJECT TOOLS
# ============================================================================

@mcp.tool()
def list_projects(account_id: int, limit: Optional[int] = None, state: Optional[str] = None) -> ProjectList:
    """List all projects for an account"""
    try:
        params = {}
        if limit:
            params["limit"] = limit
        if state:
            params["state"] = state
            
        response = make_request("GET", f"/{account_id}/projects", params=params)
        projects = [Project.model_validate(project) for project in _unwrap_list(response, "projects")]
        return ProjectList(projects=projects)
    except ApiError as e:
        raise _tool_error("list projects", e) from e


@mcp.tool()
def find_projects(
    account_id: int,
    query: str,
    exact: bool = False,
    active_only: bool = True,
    limit: int = DEFAULT_LOOKUP_RESULTS,
) -> ProjectMatchList:
    """Find projects by name without returning full project payloads."""
    try:
        normalized_query = query.strip().lower()
        if not normalized_query:
            raise ApiError("query cannot be empty")
        if not 1 <= limit <= MAX_LOOKUP_RESULTS:
            raise ApiError(f"limit must be between 1 and {MAX_LOOKUP_RESULTS}")
        response = make_request(
            "GET",
            f"/{account_id}/projects",
            params={"limit": MAX_PROJECT_FETCH_RESULTS},
        )
        projects = _unwrap_list(response, "projects")
        matches = []
        for project in projects:
            name = str(project.get("name") or "")
            name_matches = name.lower() == normalized_query if exact else normalized_query in name.lower()
            if not name_matches or (active_only and project.get("active") is False):
                continue
            matches.append(ProjectMatch(
                id=project["id"],
                name=name,
                active=project.get("active"),
                client_id=project.get("client_id"),
            ))
            if len(matches) == limit:
                break
        return ProjectMatchList(matches=matches)
    except (ApiError, KeyError) as e:
        raise _tool_error("find projects", e) from e


@mcp.tool()
def get_project(account_id: int, project_id: int) -> Project:
    """Retrieve a specific project by ID"""
    try:
        response = make_request("GET", f"/{account_id}/projects/{project_id}")
        return Project.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get project {project_id}", e) from e


@mcp.tool()
def create_project(account_id: int, name: str, description: Optional[str] = None, client_id: Optional[int] = None, active: bool = True) -> Project:
    """Create a new project"""
    try:
        data = {
            "project": {
                "name": name,
                "active": active
            }
        }
        if description:
            data["project"]["description"] = description
        if client_id:
            data["project"]["client_id"] = client_id
            
        response = make_request("POST", f"/{account_id}/projects", data=data, account_id=account_id)
        return Project.model_validate(response)
    except ApiError as e:
        raise _tool_error("create project", e) from e


@mcp.tool()
def update_project(account_id: int, project_id: int, name: Optional[str] = None, description: Optional[str] = None, active: Optional[bool] = None) -> Project:
    """Update an existing project"""
    try:
        data = {"project": {}}
        if name is not None:
            data["project"]["name"] = name
        if description is not None:
            data["project"]["description"] = description
        if active is not None:
            data["project"]["active"] = active
            
        response = make_request("PUT", f"/{account_id}/projects/{project_id}", data=data, account_id=account_id)
        return Project.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update project {project_id}", e) from e


@mcp.tool()
def delete_project(account_id: int, project_id: int) -> dict[str, str]:
    """Delete a project"""
    try:
        make_request("DELETE", f"/{account_id}/projects/{project_id}", account_id=account_id)
        return {"result": f"Project {project_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete project {project_id}", e) from e


# ============================================================================
# USER TOOLS
# ============================================================================

@mcp.tool()
def list_users(account_id: int) -> UserList:
    """List all users for an account"""
    try:
        response = make_request("GET", f"/{account_id}/users")
        users = [User.model_validate(user) for user in _unwrap_list(response, "users")]
        return UserList(users=users)
    except ApiError as e:
        raise _tool_error("list users", e) from e


@mcp.tool()
def get_user(account_id: int, user_id: int) -> User:
    """Retrieve a specific user by ID"""
    try:
        response = make_request("GET", f"/{account_id}/users/{user_id}")
        return User.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get user {user_id}", e) from e


@mcp.tool()
def get_current_user(account_id: int) -> User:
    """Retrieve the current authenticated user"""
    try:
        response = make_request("GET", f"/{account_id}/users/current")
        return User.model_validate(response)
    except ApiError as e:
        raise _tool_error("get current user", e) from e


@mcp.tool()
def create_user(account_id: int, name: str, email: str, user_level: str = "normal") -> User:
    """Create/invite a new user"""
    try:
        data = {
            "user": {
                "name": name,
                "email": email,
                "user_level": user_level
            }
        }
        response = make_request("POST", f"/{account_id}/users", data=data, account_id=account_id)
        return User.model_validate(response)
    except ApiError as e:
        raise _tool_error("create user", e) from e


@mcp.tool()
def update_user(account_id: int, user_id: int, name: Optional[str] = None, email: Optional[str] = None, user_level: Optional[str] = None) -> User:
    """Update an existing user"""
    try:
        data = {"user": {}}
        if name is not None:
            data["user"]["name"] = name
        if email is not None:
            data["user"]["email"] = email
        if user_level is not None:
            data["user"]["user_level"] = user_level
            
        response = make_request("PUT", f"/{account_id}/users/{user_id}", data=data, account_id=account_id)
        return User.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update user {user_id}", e) from e


@mcp.tool()
def delete_user(account_id: int, user_id: int) -> dict[str, str]:
    """Delete a user"""
    try:
        make_request("DELETE", f"/{account_id}/users/{user_id}", account_id=account_id)
        return {"result": f"User {user_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete user {user_id}", e) from e


# ============================================================================
# EVENT TOOLS
# ============================================================================

def _resolve_event_range(since: Optional[str], upto: Optional[str]) -> tuple[str, str]:
    if (since is None) != (upto is None):
        raise ApiError("since and upto must be provided together")
    if since is None:
        end = date.today()
        start = end - timedelta(days=DEFAULT_EVENT_LOOKBACK_DAYS - 1)
        since, upto = start.isoformat(), end.isoformat()
    _date_range(since, upto)
    return since, upto


def _fetch_events_raw(
    *,
    account_id: int,
    since: str,
    upto: str,
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    _date_range(since, upto)
    params: Dict[str, Any] = {"since": since, "upto": upto}
    if user_id is not None:
        params["user_id"] = user_id
    if project_id is not None:
        params["project_id"] = project_id
    response = make_request("GET", f"/{account_id}/hours", params=params)
    events = _unwrap_list(response, "events")
    if user_id is not None:
        events = [event for event in events if _normalize_event(event).get("user_id") == user_id]
    if project_id is not None:
        events = [event for event in events if _normalize_event(event).get("project_id") == project_id]
    return events


def _resolve_user_id(account_id: int, user_id: Optional[int]) -> int:
    if user_id is not None:
        return user_id
    response = make_request("GET", f"/{account_id}/users/current")
    try:
        return int(response["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("Could not resolve the current Timely user") from exc


def _parse_csv_values(value: Optional[str]) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _parse_workdays(workdays: str) -> set[str]:
    values = {item.upper() for item in _parse_csv_values(workdays)}
    invalid = values.difference(WEEKDAY_CODES)
    if invalid or not values:
        raise ApiError(
            f"workdays must contain comma-separated values from {','.join(WEEKDAY_CODES)}"
        )
    return values


def _parse_excluded_dates(excluded_dates: Optional[str]) -> set[str]:
    values = set(_parse_csv_values(excluded_dates))
    for value in values:
        _parse_date(value, "excluded date")
    return values

@mcp.tool()
def list_events(
    account_id: int,
    since: Optional[str] = None,
    upto: Optional[str] = None,
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    limit: int = DEFAULT_EVENT_RESULTS,
) -> EventList:
    """List time entries as compact records.

    Both since and upto must be provided together. When omitted, the most
    recent 30 days are returned. Date ranges and result counts are bounded.
    """
    try:
        resolved_since, resolved_upto = _resolve_event_range(since, upto)
        if not 1 <= limit <= MAX_EVENT_RESULTS:
            raise ApiError(f"limit must be between 1 and {MAX_EVENT_RESULTS}")
        response = _fetch_events_raw(
            account_id=account_id,
            since=resolved_since,
            upto=resolved_upto,
            user_id=user_id,
            project_id=project_id,
        )
        return EventList(
            events=[_event_model(event) for event in response[:limit]],
            has_more=len(response) > limit,
            since=resolved_since,
            upto=resolved_upto,
        )
    except ApiError as e:
        raise _tool_error("list events", e) from e


@mcp.tool()
def get_event(account_id: int, event_id: int) -> Event:
    """Retrieve a specific event by ID"""
    try:
        response = make_request("GET", f"/{account_id}/hours/{event_id}")
        return _event_model(response)
    except ApiError as e:
        raise _tool_error(f"get event {event_id}", e) from e


@mcp.tool()
def create_event(
    account_id: int,
    day: str,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    note: Optional[str] = None,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    label_ids: Optional[str] = None,
    duration_minutes: Optional[int] = None,
) -> Event:
    """Create a new event (time entry).

    Prefer duration_minutes. Clock times and the legacy hours/minutes encoding
    remain available for compatibility.
    
    label_ids: comma-separated list of label IDs (e.g. "2531527,2531529").
    """
    try:
        return _create_event_api(
            account_id=account_id,
            day=day,
            user_id=user_id,
            project_id=project_id,
            note=note,
            label_ids=label_ids,
            duration_minutes=duration_minutes,
            from_time=from_time,
            to_time=to_time,
        )
    except ApiError as e:
        raise _tool_error("create event", e) from e


@mcp.tool()
def update_event(
    account_id: int,
    event_id: int,
    day: Optional[str] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    note: Optional[str] = None,
    project_id: Optional[int] = None,
    label_ids: Optional[str] = None,
    duration_minutes: Optional[int] = None,
) -> Event:
    """Update an existing event.

    Duration can be set two ways:
      * clock times: from_time/to_time as "HH:MM" strings, OR
      * direct encoding: from_time="hours:HH" and to_time="minutes:MM"
        (e.g. from_time="hours:3", to_time="minutes:30" for 3h30m).

    project_id: optionally move the entry to a different project.
    label_ids: comma-separated list of label IDs (e.g. "2531438,4226873").
               Replaces the entry's labels when provided.
    """
    try:
        data: Dict[str, Any] = {"event": {}}
        if day is not None:
            _parse_date(day, "day")
            data["event"]["day"] = day

        if duration_minutes is not None or from_time is not None or to_time is not None:
            data["event"].update(_duration_fields(duration_minutes, from_time, to_time))

        if note is not None:
            data["event"]["note"] = note
        if project_id is not None:
            data["event"]["project_id"] = project_id
        if label_ids is not None:
            data["event"]["label_ids"] = _parse_label_ids(label_ids)

        if not data["event"]:
            raise ApiError("No event changes were provided")

        response = make_request("PUT", f"/{account_id}/hours/{event_id}", data=data, account_id=account_id)
        return _event_model(response)
    except ApiError as e:
        raise _tool_error(f"update event {event_id}", e) from e


@mcp.tool()
def delete_event(account_id: int, event_id: int) -> dict[str, str]:
    """Delete an event"""
    try:
        make_request("DELETE", f"/{account_id}/hours/{event_id}", account_id=account_id)
        return {"result": f"Event {event_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete event {event_id}", e) from e


@mcp.tool()
def start_timer(account_id: int, event_id: int) -> Event:
    """Start timer on an event"""
    try:
        response = make_request("PUT", f"/{account_id}/hours/{event_id}/start", account_id=account_id)
        return _event_model(response)
    except ApiError as e:
        raise _tool_error(f"start timer for event {event_id}", e) from e


@mcp.tool()
def stop_timer(account_id: int, event_id: int) -> Event:
    """Stop timer on an event"""
    try:
        response = make_request("PUT", f"/{account_id}/hours/{event_id}/stop", account_id=account_id)
        return _event_model(response)
    except ApiError as e:
        raise _tool_error(f"stop timer for event {event_id}", e) from e


def _event_minutes(event: Dict[str, Any]) -> int:
    """Extract an event's logged duration in whole minutes.

    Handles Timely's two shapes: a nested ``duration`` object
    ({hours, minutes, seconds}) or flat ``hours``/``minutes`` fields.
    """
    dur = event.get("duration")
    if isinstance(dur, dict):
        seconds = int(dur.get("seconds", 0) or 0)
        return (
            int(dur.get("hours", 0) or 0) * MINUTES_PER_HOUR
            + int(dur.get("minutes", 0) or 0)
            + (1 if seconds >= ROUND_UP_SECONDS_THRESHOLD else 0)
        )
    if isinstance(dur, (int, float)):
        # Timely reports bare durations in minutes
        return int(dur)
    seconds = int(event.get("seconds", 0) or 0)
    return (
        int(event.get("hours", 0) or 0) * MINUTES_PER_HOUR
        + int(event.get("minutes", 0) or 0)
        + (1 if seconds >= ROUND_UP_SECONDS_THRESHOLD else 0)
    )


class DailyHours(BaseModel):
    """Logged time for a single day with the gap to a target."""
    day: str = Field(description="Date (YYYY-MM-DD)")
    logged_minutes: int = Field(description="Total minutes logged that day")
    logged_hm: str = Field(description="Logged time formatted as Hh Mm")
    target_minutes: int = Field(description="Target minutes for the day")
    gap_minutes: int = Field(description="target_minutes - logged_minutes (positive = unfilled)")
    event_count: int = Field(description="Number of entries on the day")
    is_workday: bool = Field(description="Whether the date is configured as a workday")
    is_excluded: bool = Field(description="Whether the date was explicitly excluded")


class DailyHoursReport(BaseModel):
    """Per-day logged hours across a date range."""
    days: List[DailyHours] = Field(description="One entry per day with logged time")
    total_logged_minutes: int = Field(description="Sum of logged minutes across the range")


class EventSpec(BaseModel):
    """Validated duration-based event used by bulk and backfill tools."""

    day: str = Field(description="Event date in YYYY-MM-DD format")
    duration_minutes: int = Field(description="Duration in minutes", ge=1, le=MAX_EVENT_DURATION_MINUTES)
    note: str = Field(description="Event note", default="")
    project_id: Optional[int] = Field(description="Project ID", default=None)
    user_id: Optional[int] = Field(description="User ID; defaults to the selected backfill user", default=None)
    label_ids: List[int] = Field(description="Label IDs", default_factory=list)


class BackfillDay(BaseModel):
    day: str
    target_minutes: int
    existing_minutes: int
    proposed_minutes: int
    remaining_gap_minutes: int
    is_workday: bool
    is_excluded: bool


class BackfillPlan(BaseModel):
    plan_hash: str = Field(description="Hash required to apply this exact plan")
    account_id: int
    user_id: int
    since: str
    upto: str
    events: List[EventSpec] = Field(description="New events proposed for creation")
    days: List[BackfillDay]
    skipped_exact_duplicates: int = 0
    warnings: List[str] = Field(default_factory=list)


class BulkEventResult(BaseModel):
    index: int
    status: Literal["planned", "created", "skipped", "reconciled", "failed", "rolled_back", "rollback_failed"]
    event_id: Optional[int] = None
    message: Optional[str] = None
    event: EventSpec


class BulkEventReport(BaseModel):
    dry_run: bool
    success: bool
    created_count: int = 0
    skipped_count: int = 0
    rolled_back_count: int = 0
    results: List[BulkEventResult] = Field(default_factory=list)


class _CachedBackfillPlan(BaseModel):
    plan_hash: str
    account_id: int
    user_id: int
    since: str
    upto: str
    target_minutes: int
    existing: List[str] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)


_backfill_plan_cache: Dict[str, _CachedBackfillPlan] = {}


def _validated_event_spec(spec: EventSpec, default_user_id: Optional[int] = None) -> EventSpec:
    _parse_date(spec.day, "event day")
    labels = _parse_label_ids(spec.label_ids)
    return spec.model_copy(update={
        "note": spec.note.strip(),
        "user_id": spec.user_id if spec.user_id is not None else default_user_id,
        "label_ids": labels,
    })


def _event_fingerprint(spec: EventSpec) -> str:
    canonical = {
        "day": spec.day,
        "duration_minutes": spec.duration_minutes,
        "note": spec.note.strip(),
        "project_id": spec.project_id,
        "user_id": spec.user_id,
        "label_ids": sorted(spec.label_ids),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _raw_event_spec(event: Dict[str, Any]) -> EventSpec:
    normalized = _normalize_event(event)
    return EventSpec(
        day=normalized["day"],
        duration_minutes=normalized["duration_minutes"],
        note=(normalized.get("note") or "").strip(),
        project_id=normalized.get("project_id"),
        user_id=normalized.get("user_id"),
        label_ids=normalized.get("label_ids") or [],
    )


def _event_counter(events: List[Dict[str, Any]]) -> Counter[str]:
    return Counter(_event_fingerprint(_raw_event_spec(event)) for event in events)


def _specs_by_day(specs: List[EventSpec]) -> Dict[str, List[EventSpec]]:
    grouped: Dict[str, List[EventSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.day, []).append(spec)
    return grouped


def _remember_backfill_plan(plan: BackfillPlan, cached: _CachedBackfillPlan) -> None:
    _backfill_plan_cache[plan.plan_hash] = cached
    while len(_backfill_plan_cache) > MAX_CACHED_BACKFILL_PLANS:
        _backfill_plan_cache.pop(next(iter(_backfill_plan_cache)))


def _plan_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _build_backfill_plan(
    *,
    account_id: int,
    since: str,
    upto: str,
    user_id: int,
    entries: List[EventSpec],
    target_minutes: int,
    workdays: str,
    excluded_dates: Optional[str],
    filler_project_id: Optional[int],
    filler_note: str,
    filler_label_ids: Optional[List[int]],
) -> BackfillPlan:
    if len(entries) > MAX_BULK_EVENTS:
        raise ApiError(f"A backfill plan cannot contain more than {MAX_BULK_EVENTS} fixed entries")
    if not 1 <= target_minutes <= MAX_EVENT_DURATION_MINUTES:
        raise ApiError(f"target_minutes must be between 1 and {MAX_EVENT_DURATION_MINUTES}")
    calendar_days = _date_range(since, upto)
    calendar_set = {day.isoformat() for day in calendar_days}
    configured_workdays = _parse_workdays(workdays)
    exclusions = _parse_excluded_dates(excluded_dates)
    normalized_entries = [_validated_event_spec(spec, user_id) for spec in entries]
    for spec in normalized_entries:
        if spec.day not in calendar_set:
            raise ApiError(f"Fixed event date {spec.day} is outside the plan range")
        if spec.user_id != user_id:
            raise ApiError("All backfill entries must use the selected user_id")
    filler_labels = _parse_label_ids(filler_label_ids)

    existing = _fetch_events_raw(
        account_id=account_id,
        since=since,
        upto=upto,
        user_id=user_id,
    )
    existing_counter = _event_counter(existing)
    existing_minutes: Dict[str, int] = {day: 0 for day in calendar_set}
    for event in existing:
        event_day = event.get("day")
        if event_day in existing_minutes:
            existing_minutes[event_day] += _event_minutes(event)

    skipped_duplicates = 0
    proposed: List[EventSpec] = []
    working_counter = existing_counter.copy()
    for spec in normalized_entries:
        fingerprint = _event_fingerprint(spec)
        if working_counter[fingerprint] > 0:
            working_counter[fingerprint] -= 1
            skipped_duplicates += 1
            continue
        proposed.append(spec)

    grouped = _specs_by_day(proposed)
    days: List[BackfillDay] = []
    warnings: List[str] = []
    final_events: List[EventSpec] = []
    for calendar_day in calendar_days:
        day = calendar_day.isoformat()
        is_workday = WEEKDAY_CODES[calendar_day.weekday()] in configured_workdays
        is_excluded = day in exclusions
        daily_target = target_minutes if is_workday and not is_excluded else 0
        day_existing = existing_minutes[day]
        fixed = grouped.get(day, [])
        fixed_minutes = sum(spec.duration_minutes for spec in fixed)
        if day_existing + fixed_minutes > target_minutes:
            raise ApiError(
                f"Plan exceeds the {target_minutes}-minute daily cap on {day}: "
                f"{day_existing} existing + {fixed_minutes} proposed"
            )
        final_events.extend(fixed)
        gap = max(daily_target - day_existing - fixed_minutes, 0)
        if gap > 0 and filler_project_id is not None:
            filler = EventSpec(
                day=day,
                duration_minutes=gap,
                note=filler_note.strip(),
                project_id=filler_project_id,
                user_id=user_id,
                label_ids=filler_labels,
            )
            final_events.append(filler)
            fixed_minutes += gap
            gap = 0
        elif gap > 0:
            warnings.append(f"{day} remains {gap} minutes under target because no filler project was supplied")
        if daily_target > 0 and day_existing > daily_target:
            warnings.append(f"{day} already exceeds its configured target by {day_existing - daily_target} minutes")
        days.append(BackfillDay(
            day=day,
            target_minutes=daily_target,
            existing_minutes=day_existing,
            proposed_minutes=fixed_minutes,
            remaining_gap_minutes=gap,
            is_workday=is_workday,
            is_excluded=is_excluded,
        ))

    hash_payload = {
        "account_id": account_id,
        "user_id": user_id,
        "since": since,
        "upto": upto,
        "target_minutes": target_minutes,
        "events": [spec.model_dump(mode="json") for spec in final_events],
        "existing": sorted(existing_counter.elements()),
    }
    plan_hash = _plan_hash(hash_payload)
    cached = _CachedBackfillPlan(
        plan_hash=plan_hash,
        account_id=account_id,
        user_id=user_id,
        since=since,
        upto=upto,
        target_minutes=target_minutes,
        existing=hash_payload["existing"],
        events=hash_payload["events"],
    )
    plan = BackfillPlan(
        plan_hash=plan_hash,
        account_id=account_id,
        user_id=user_id,
        since=since,
        upto=upto,
        events=final_events,
        days=days,
        skipped_exact_duplicates=skipped_duplicates,
        warnings=warnings,
    )
    _remember_backfill_plan(plan, cached)
    return plan


def _matching_event_id(events: List[Dict[str, Any]], spec: EventSpec, ignored_ids: set[int]) -> Optional[int]:
    fingerprint = _event_fingerprint(spec)
    for event in events:
        event_id = event.get("id")
        if event_id in ignored_ids:
            continue
        if _event_fingerprint(_raw_event_spec(event)) == fingerprint:
            return int(event_id)
    return None


def _bulk_preflight(
    *,
    account_id: int,
    entries: List[EventSpec],
    default_user_id: Optional[int],
    max_daily_minutes: int,
    duplicate_policy: Literal["skip_exact", "error"],
) -> tuple[List[BulkEventResult], set[int]]:
    if not entries:
        raise ApiError("entries cannot be empty")
    if len(entries) > MAX_BULK_EVENTS:
        raise ApiError(f"A bulk request cannot contain more than {MAX_BULK_EVENTS} entries")
    if not 1 <= max_daily_minutes <= MAX_EVENT_DURATION_MINUTES:
        raise ApiError(f"max_daily_minutes must be between 1 and {MAX_EVENT_DURATION_MINUTES}")
    normalized = [_validated_event_spec(entry, default_user_id) for entry in entries]
    if any(entry.user_id is None for entry in normalized):
        raise ApiError("Each bulk event needs user_id or default_user_id")
    since = min(entry.day for entry in normalized)
    upto = max(entry.day for entry in normalized)
    _date_range(since, upto)

    existing: List[Dict[str, Any]] = []
    for user_id in sorted({entry.user_id for entry in normalized}):
        existing.extend(_fetch_events_raw(
            account_id=account_id,
            since=since,
            upto=upto,
            user_id=user_id,
        ))
    existing_ids = {int(event["id"]) for event in existing if event.get("id") is not None}
    existing_counter = _event_counter(existing)
    daily_totals: Dict[tuple[int, str], int] = {}
    for event in existing:
        spec = _raw_event_spec(event)
        if spec.user_id is not None:
            key = (spec.user_id, spec.day)
            daily_totals[key] = daily_totals.get(key, 0) + spec.duration_minutes

    results: List[BulkEventResult] = []
    planned_fingerprints: set[str] = set()
    for index, entry in enumerate(normalized):
        fingerprint = _event_fingerprint(entry)
        if existing_counter[fingerprint] > 0 or fingerprint in planned_fingerprints:
            if duplicate_policy == "error":
                raise ApiError(f"Entry {index} exactly duplicates an existing or planned event")
            if existing_counter[fingerprint] > 0:
                existing_counter[fingerprint] -= 1
            results.append(BulkEventResult(
                index=index,
                status="skipped",
                message="Exact duplicate already exists or is already planned",
                event=entry,
            ))
            continue
        key = (entry.user_id, entry.day)
        resulting_total = daily_totals.get(key, 0) + entry.duration_minutes
        if resulting_total > max_daily_minutes:
            raise ApiError(
                f"Entry {index} would raise {entry.day} to {resulting_total} minutes, "
                f"above max_daily_minutes={max_daily_minutes}"
            )
        daily_totals[key] = resulting_total
        planned_fingerprints.add(fingerprint)
        results.append(BulkEventResult(index=index, status="planned", event=entry))
    return results, existing_ids


def _rollback_created(account_id: int, created_results: List[BulkEventResult]) -> int:
    rolled_back = 0
    for result in reversed(created_results):
        if result.event_id is None:
            continue
        try:
            make_request(
                "DELETE",
                f"/{account_id}/hours/{result.event_id}",
                account_id=account_id,
            )
            result.status = "rolled_back"
            result.message = "Deleted after a later batch failure"
            rolled_back += 1
        except ApiError as rollback_error:
            result.status = "rollback_failed"
            result.message = str(rollback_error)
    return rolled_back


def _execute_bulk_events(
    *,
    account_id: int,
    entries: List[EventSpec],
    default_user_id: Optional[int],
    dry_run: bool,
    max_daily_minutes: int,
    duplicate_policy: Literal["skip_exact", "error"],
    rollback_on_failure: bool,
) -> BulkEventReport:
    results, existing_ids = _bulk_preflight(
        account_id=account_id,
        entries=entries,
        default_user_id=default_user_id,
        max_daily_minutes=max_daily_minutes,
        duplicate_policy=duplicate_policy,
    )
    skipped_count = sum(result.status == "skipped" for result in results)
    if dry_run:
        return BulkEventReport(
            dry_run=True,
            success=True,
            skipped_count=skipped_count,
            results=results,
        )

    created_results: List[BulkEventResult] = []
    try:
        for result in results:
            if result.status != "planned":
                continue
            entry = result.event
            try:
                created = _create_event_api(
                    account_id=account_id,
                    day=entry.day,
                    user_id=entry.user_id,
                    project_id=entry.project_id,
                    note=entry.note,
                    label_ids=entry.label_ids,
                    duration_minutes=entry.duration_minutes,
                )
                result.status = "created"
                result.event_id = created.id
                created_results.append(result)
                existing_ids.add(created.id)
            except ApiError as create_error:
                if create_error.ambiguous_write:
                    refreshed = _fetch_events_raw(
                        account_id=account_id,
                        since=entry.day,
                        upto=entry.day,
                        user_id=entry.user_id,
                    )
                    reconciled_id = _matching_event_id(refreshed, entry, existing_ids)
                    if reconciled_id is not None:
                        result.status = "reconciled"
                        result.event_id = reconciled_id
                        result.message = "Write timed out but the event was found afterward"
                        created_results.append(result)
                        existing_ids.add(reconciled_id)
                        continue
                result.status = "failed"
                result.message = str(create_error)
                raise
    except ApiError:
        rolled_back = _rollback_created(account_id, created_results) if rollback_on_failure else 0
        return BulkEventReport(
            dry_run=False,
            success=False,
            created_count=sum(result.status in {"created", "reconciled"} for result in results),
            skipped_count=skipped_count,
            rolled_back_count=rolled_back,
            results=results,
        )

    return BulkEventReport(
        dry_run=False,
        success=True,
        created_count=len(created_results),
        skipped_count=skipped_count,
        results=results,
    )


@mcp.tool()
def get_daily_hours(
    account_id: int,
    since: str,
    upto: str,
    user_id: Optional[int] = None,
    target_hours: float = 8.0,
    only_gaps: bool = False,
    workdays: str = DEFAULT_WORKDAYS,
    excluded_dates: Optional[str] = None,
) -> DailyHoursReport:
    """Summarize logged time per day over a date range and the gap to a daily target.

    This aggregates the /hours entries by day so you can see, at a glance, which
    days are under the target (e.g. days that still need filling to 8h).

    Args:
        since/upto: inclusive date range (YYYY-MM-DD).
        user_id: restrict to one user (recommended).
        target_hours: per-day target used to compute the gap (default 8.0).
        only_gaps: when True, only return days whose logged time is below target.
    """
    try:
        calendar_days = _date_range(since, upto)
        resolved_user_id = _resolve_user_id(account_id, user_id)
        response = _fetch_events_raw(
            account_id=account_id,
            since=since,
            upto=upto,
            user_id=resolved_user_id,
        )
        configured_workdays = _parse_workdays(workdays)
        exclusions = _parse_excluded_dates(excluded_dates)
        target_minutes = int(round(target_hours * MINUTES_PER_HOUR))
        if target_minutes < 0 or target_minutes > MAX_EVENT_DURATION_MINUTES:
            raise ApiError("target_hours must be between 0 and 24")
        totals: Dict[str, Dict[str, int]] = {
            day.isoformat(): {"minutes": 0, "count": 0} for day in calendar_days
        }
        for event in response:
            day = event.get("day")
            if not day or day not in totals:
                continue
            bucket = totals[day]
            bucket["minutes"] += _event_minutes(event)
            bucket["count"] += 1

        days: List[DailyHours] = []
        total_logged = 0
        for calendar_day in calendar_days:
            day = calendar_day.isoformat()
            mins = totals[day]["minutes"]
            total_logged += mins
            is_workday = WEEKDAY_CODES[calendar_day.weekday()] in configured_workdays
            is_excluded = day in exclusions
            daily_target = target_minutes if is_workday and not is_excluded else 0
            gap = daily_target - mins
            if only_gaps and gap <= 0:
                continue
            days.append(DailyHours(
                day=day,
                logged_minutes=mins,
                logged_hm=f"{mins // MINUTES_PER_HOUR}h {mins % MINUTES_PER_HOUR:02d}m",
                target_minutes=daily_target,
                gap_minutes=gap,
                event_count=totals[day]["count"],
                is_workday=is_workday,
                is_excluded=is_excluded,
            ))

        return DailyHoursReport(days=days, total_logged_minutes=total_logged)
    except ApiError as e:
        raise _tool_error("get daily hours", e) from e


@mcp.tool()
def plan_timesheet_backfill(
    account_id: int,
    since: str,
    upto: str,
    entries: List[EventSpec],
    user_id: Optional[int] = None,
    target_minutes: int = DEFAULT_DAILY_TARGET_MINUTES,
    workdays: str = DEFAULT_WORKDAYS,
    excluded_dates: Optional[str] = None,
    filler_project_id: Optional[int] = None,
    filler_note: str = "",
    filler_label_ids: Optional[List[int]] = None,
) -> BackfillPlan:
    """Create a dry-run backfill plan and return the hash required to apply it.

    All projects, labels, fixed entries, workdays, exclusions, and filler
    behavior are caller supplied. Existing exact duplicates are skipped.
    """
    try:
        resolved_user_id = _resolve_user_id(account_id, user_id)
        return _build_backfill_plan(
            account_id=account_id,
            since=since,
            upto=upto,
            user_id=resolved_user_id,
            entries=entries,
            target_minutes=target_minutes,
            workdays=workdays,
            excluded_dates=excluded_dates,
            filler_project_id=filler_project_id,
            filler_note=filler_note,
            filler_label_ids=filler_label_ids,
        )
    except ApiError as e:
        raise _tool_error("plan timesheet backfill", e) from e


@mcp.tool()
def bulk_create_events(
    account_id: int,
    entries: List[EventSpec],
    default_user_id: Optional[int] = None,
    dry_run: bool = True,
    max_daily_minutes: int = DEFAULT_DAILY_TARGET_MINUTES,
    duplicate_policy: Literal["skip_exact", "error"] = "skip_exact",
    rollback_on_failure: bool = True,
) -> BulkEventReport:
    """Validate and create multiple duration-based events safely.

    Dry-run is the default. Apply mode prefetches existing entries, enforces
    the daily cap, skips or rejects exact duplicates, reconciles ambiguous
    write timeouts, and optionally rolls back this batch after a failure.
    """
    try:
        if not dry_run:
            _ensure_write_allowed(account_id)
        resolved_user_id = (
            _resolve_user_id(account_id, default_user_id)
            if default_user_id is not None or any(entry.user_id is None for entry in entries)
            else None
        )
        return _execute_bulk_events(
            account_id=account_id,
            entries=entries,
            default_user_id=resolved_user_id,
            dry_run=dry_run,
            max_daily_minutes=max_daily_minutes,
            duplicate_policy=duplicate_policy,
            rollback_on_failure=rollback_on_failure,
        )
    except ApiError as e:
        raise _tool_error("bulk create events", e) from e


@mcp.tool()
def apply_timesheet_plan(
    plan_hash: str,
    rollback_on_failure: bool = True,
) -> BulkEventReport:
    """Apply a previously returned backfill plan after revalidating live data."""
    try:
        cached = _backfill_plan_cache.get(plan_hash)
        if cached is None:
            raise ApiError("Unknown or expired plan_hash; run plan_timesheet_backfill again")
        _ensure_write_allowed(cached.account_id)
        events = [EventSpec.model_validate(spec) for spec in cached.events]
        live_events = _fetch_events_raw(
            account_id=cached.account_id,
            since=cached.since,
            upto=cached.upto,
            user_id=cached.user_id,
        )
        live_payload = {
            "account_id": cached.account_id,
            "user_id": cached.user_id,
            "since": cached.since,
            "upto": cached.upto,
            "target_minutes": cached.target_minutes,
            "events": [spec.model_dump(mode="json") for spec in events],
            "existing": sorted(_event_counter(live_events).elements()),
        }
        if _plan_hash(live_payload) != plan_hash:
            raise ApiError("Timely entries changed after planning; create a fresh plan before applying")
        report = _execute_bulk_events(
            account_id=cached.account_id,
            entries=events,
            default_user_id=cached.user_id,
            dry_run=False,
            max_daily_minutes=cached.target_minutes,
            duplicate_policy="skip_exact",
            rollback_on_failure=rollback_on_failure,
        )
        if report.success:
            _backfill_plan_cache.pop(plan_hash, None)
        return report
    except ApiError as e:
        raise _tool_error("apply timesheet plan", e) from e


# ============================================================================
# TEAM TOOLS
# ============================================================================

@mcp.tool()
def list_teams(account_id: int) -> TeamList:
    """List all teams for an account"""
    try:
        response = make_request("GET", f"/{account_id}/teams")
        teams = [Team.model_validate(team) for team in _unwrap_list(response, "teams")]
        return TeamList(teams=teams)
    except ApiError as e:
        raise _tool_error("list teams", e) from e


@mcp.tool()
def get_team(account_id: int, team_id: int) -> Team:
    """Retrieve a specific team by ID"""
    try:
        response = make_request("GET", f"/{account_id}/teams/{team_id}")
        return Team.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get team {team_id}", e) from e


@mcp.tool()
def create_team(account_id: int, name: str) -> Team:
    """Create a new team"""
    try:
        data = {
            "team": {
                "name": name
            }
        }
        response = make_request("POST", f"/{account_id}/teams", data=data, account_id=account_id)
        return Team.model_validate(response)
    except ApiError as e:
        raise _tool_error("create team", e) from e


@mcp.tool()
def update_team(account_id: int, team_id: int, name: str) -> Team:
    """Update an existing team"""
    try:
        data = {
            "team": {
                "name": name
            }
        }
        response = make_request("PUT", f"/{account_id}/teams/{team_id}", data=data, account_id=account_id)
        return Team.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update team {team_id}", e) from e


@mcp.tool()
def delete_team(account_id: int, team_id: int) -> dict[str, str]:
    """Delete a team"""
    try:
        make_request("DELETE", f"/{account_id}/teams/{team_id}", account_id=account_id)
        return {"result": f"Team {team_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete team {team_id}", e) from e


# ============================================================================
# LABEL TOOLS
# ============================================================================

def _flatten_labels(
    labels_data: List[Dict[str, Any]],
    parent: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Recursively flatten labels without mutating the API response."""
    result: List[Dict[str, Any]] = []
    for source in labels_data:
        label = dict(source)
        children = label.pop("children", None) or []
        if label.get("parent_id") is None and parent is not None:
            label["parent_id"] = parent.get("id")
        result.append(label)
        result.extend(_flatten_labels(children, source))
    return result


@mcp.tool()
def list_labels(account_id: int) -> LabelList:
    """List all labels/tags for an account, including child labels (customers, environments, etc.)"""
    try:
        response = make_request("GET", f"/{account_id}/labels")
        flat = _flatten_labels(_unwrap_list(response, "labels"))
        labels = [Label.model_validate(label) for label in flat]
        return LabelList(labels=labels)
    except ApiError as e:
        raise _tool_error("list labels", e) from e


@mcp.tool()
def get_label(account_id: int, label_id: int) -> Label:
    """Retrieve a specific label by ID"""
    try:
        response = make_request("GET", f"/{account_id}/labels/{label_id}")
        return Label.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"get label {label_id}", e) from e


class LabelMatch(BaseModel):
    """A label matched by name search, with its parent context."""
    id: int = Field(description="Label ID")
    name: str = Field(description="Label name")
    parent_id: Optional[int] = Field(description="Parent label ID (null for top-level)", default=None)
    parent_name: Optional[str] = Field(description="Parent label name, if any", default=None)


class LabelMatchList(BaseModel):
    """Result of a label name search."""
    matches: List[LabelMatch] = Field(description="Labels whose name matched the query")


@mcp.tool()
def find_labels(
    account_id: int,
    query: str,
    parent_id: Optional[int] = None,
    exact: bool = False,
    limit: int = DEFAULT_LOOKUP_RESULTS,
) -> LabelMatchList:
    """Search labels by name and return their IDs with parent context.

    Solves the common "what's the label ID for customer/environment X?" lookup
    without dumping the whole label tree. Matching is case-insensitive substring.

    Args:
        query: substring to match against label names (case-insensitive).
        parent_id: optionally restrict results to children of this parent group
                   (e.g. the customer group id). Use this to disambiguate names
                   that exist under multiple groups.
    """
    try:
        response = make_request("GET", f"/{account_id}/labels")
        q = query.strip().lower()
        if not q:
            raise ApiError("query cannot be empty")
        if not 1 <= limit <= MAX_LOOKUP_RESULTS:
            raise ApiError(f"limit must be between 1 and {MAX_LOOKUP_RESULTS}")
        flat = _flatten_labels(_unwrap_list(response, "labels"))
        names_by_id = {label.get("id"): label.get("name") for label in flat}

        matches: List[LabelMatch] = []

        for label in flat:
            name = label.get("name") or ""
            pid = label.get("parent_id")
            if parent_id is not None and pid != parent_id:
                continue
            name_matches = name.lower() == q if exact else q in name.lower()
            if name_matches:
                matches.append(LabelMatch(
                    id=label["id"],
                    name=name,
                    parent_id=pid,
                    parent_name=names_by_id.get(pid),
                ))
                if len(matches) == limit:
                    break

        return LabelMatchList(matches=matches)
    except (ApiError, KeyError) as e:
        raise _tool_error("find labels", e) from e


@mcp.tool()
def create_label(account_id: int, name: str, color: Optional[str] = None) -> Label:
    """Create a new label/tag"""
    try:
        data = {
            "label": {
                "name": name
            }
        }
        if color:
            data["label"]["color"] = color
            
        response = make_request("POST", f"/{account_id}/labels", data=data, account_id=account_id)
        return Label.model_validate(response)
    except ApiError as e:
        raise _tool_error("create label", e) from e


@mcp.tool()
def update_label(account_id: int, label_id: int, name: Optional[str] = None, color: Optional[str] = None) -> Label:
    """Update an existing label"""
    try:
        data = {"label": {}}
        if name is not None:
            data["label"]["name"] = name
        if color is not None:
            data["label"]["color"] = color
            
        response = make_request("PUT", f"/{account_id}/labels/{label_id}", data=data, account_id=account_id)
        return Label.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update label {label_id}", e) from e


@mcp.tool()
def delete_label(account_id: int, label_id: int) -> dict[str, str]:
    """Delete a label"""
    try:
        make_request("DELETE", f"/{account_id}/labels/{label_id}", account_id=account_id)
        return {"result": f"Label {label_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete label {label_id}", e) from e


# ============================================================================
# FORECAST TOOLS
# ============================================================================

@mcp.tool()
def list_forecasts(account_id: int, since: Optional[str] = None, upto: Optional[str] = None) -> ForecastList:
    """List all forecasts/tasks for an account"""
    try:
        params = {}
        if since:
            params["since"] = since
        if upto:
            params["upto"] = upto
            
        response = make_request("GET", f"/{account_id}/forecasts", params=params)
        forecasts = [Forecast.model_validate(forecast) for forecast in _unwrap_list(response, "forecasts")]
        return ForecastList(forecasts=forecasts)
    except ApiError as e:
        raise _tool_error("list forecasts", e) from e


@mcp.tool()
def create_forecast(account_id: int, project_id: int, user_id: int, day: str, duration: int, note: Optional[str] = None) -> Forecast:
    """Create a new forecast/task"""
    try:
        data = {
            "forecast": {
                "project_id": project_id,
                "user_id": user_id,
                "day": day,
                "duration": duration
            }
        }
        if note:
            data["forecast"]["note"] = note
            
        response = make_request("POST", f"/{account_id}/forecasts", data=data, account_id=account_id)
        return Forecast.model_validate(response)
    except ApiError as e:
        raise _tool_error("create forecast", e) from e


@mcp.tool()
def update_forecast(account_id: int, forecast_id: int, duration: Optional[int] = None, note: Optional[str] = None) -> Forecast:
    """Update an existing forecast"""
    try:
        data = {"forecast": {}}
        if duration is not None:
            data["forecast"]["duration"] = duration
        if note is not None:
            data["forecast"]["note"] = note
            
        response = make_request("PUT", f"/{account_id}/forecasts/{forecast_id}", data=data, account_id=account_id)
        return Forecast.model_validate(response)
    except ApiError as e:
        raise _tool_error(f"update forecast {forecast_id}", e) from e


@mcp.tool()
def delete_forecast(account_id: int, forecast_id: int) -> dict[str, str]:
    """Delete a forecast"""
    try:
        make_request("DELETE", f"/{account_id}/forecasts/{forecast_id}", account_id=account_id)
        return {"result": f"Forecast {forecast_id} deleted successfully"}
    except ApiError as e:
        raise _tool_error(f"delete forecast {forecast_id}", e) from e



# NOTE: Webhook endpoints (/{account_id}/webhooks) are not available via
# cookie-based web API auth. They require OAuth API tokens. Removed to
# avoid confusing 404 errors.


# ============================================================================
# REPORTS TOOLS
# ============================================================================

@mcp.tool()
def get_reports(account_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, user_ids: Optional[str] = None, project_ids: Optional[str] = None) -> dict[str, Any]:
    """Get reports data with optional filters"""
    try:
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if user_ids:
            params["user_ids"] = user_ids
        if project_ids:
            params["project_ids"] = project_ids
            
        response = make_request("GET", f"/{account_id}/reports", params=params)
        return {"reports": response}
    except ApiError as e:
        raise _tool_error("get reports", e) from e


# ============================================================================
# UTILITY TOOLS
# ============================================================================

@mcp.tool()
def get_permissions(account_id: int, user_id: int) -> dict[str, Any]:
    """Get user permissions for an account. A user_id is required."""
    try:
        endpoint = f"/{account_id}/users/{user_id}/permissions"
        response = make_request("GET", endpoint)
        return {"permissions": response}
    except ApiError as e:
        raise _tool_error("get permissions", e) from e


@mcp.tool()
def list_roles(account_id: int) -> dict[str, Any]:
    """List available roles for an account"""
    try:
        response = make_request("GET", f"/{account_id}/roles")
        return {"roles": response}
    except ApiError as e:
        raise _tool_error("list roles", e) from e


@mcp.tool()
def get_user_capacities(account_id: int, user_id: int, since: Optional[str] = None, upto: Optional[str] = None) -> dict[str, Any]:
    """Get user capacity information. A user_id is required."""
    try:
        params = {}
        if since:
            params["since"] = since
        if upto:
            params["upto"] = upto

        endpoint = f"/{account_id}/users/{user_id}/capacities"
        response = make_request("GET", endpoint, params=params)
        return {"capacities": response}
    except ApiError as e:
        raise _tool_error("get user capacities", e) from e


def main():
    """Main function to run the MCP server"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
