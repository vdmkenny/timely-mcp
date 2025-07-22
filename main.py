#!/usr/bin/env python3
"""
Timely API MCP Server
A Model Context Protocol server for interacting with the Timely time tracking API.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, Union

import requests
from pydantic import BaseModel, Field

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# Initialize the MCP server
mcp = FastMCP("Timely API")

# Base URL for Timely API
BASE_URL = "https://api.timelyapp.com/1.1"

# Global variable to store access token
ACCESS_TOKEN = ""


def get_connection_credentials() -> dict[str, Any]:
    """Get credentials from Nango"""
    id = os.environ.get("NANGO_CONNECTION_ID")
    integration_id = os.environ.get("NANGO_INTEGRATION_ID")
    base_url = os.environ.get("NANGO_BASE_URL")
    secret_key = os.environ.get("NANGO_SECRET_KEY")
    
    if not all([id, integration_id, base_url, secret_key]):
        raise ApiError("Missing Nango configuration. Please set NANGO_CONNECTION_ID, NANGO_INTEGRATION_ID, NANGO_BASE_URL, and NANGO_SECRET_KEY environment variables.")
    
    url = f"{base_url}/connection/{id}"
    params = {
        "provider_config_key": integration_id,
        "refresh_token": "true",
    }
    headers = {"Authorization": f"Bearer {secret_key}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()  # Raise exception for bad status codes
        return response.json()
    except requests.RequestException as e:
        raise ApiError(f"Failed to get credentials from Nango: {str(e)}")


def get_access_token() -> str:
    """Get access token from Nango"""
    global ACCESS_TOKEN
    
    # Return cached token if available
    if ACCESS_TOKEN:
        return ACCESS_TOKEN
    
    # Get from Nango
    try:
        credentials = get_connection_credentials()
        ACCESS_TOKEN = credentials.get("credentials", {}).get("access_token")
        if not ACCESS_TOKEN:
            raise ApiError("No access token found in Nango credentials")
        return ACCESS_TOKEN
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(f"Error retrieving access token: {str(e)}")


class ApiError(Exception):
    """Custom exception for API errors"""
    pass


def make_request(method: str, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Make HTTP request to Timely API with error handling"""
    try:
        access_token = get_access_token()
    except ApiError as e:
        raise ApiError(f"Authentication failed: {str(e)}")
    
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=30)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "PUT":
            response = requests.put(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "DELETE":
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            raise ApiError(f"Unsupported HTTP method: {method}")
        
        # Handle HTTP errors
        if response.status_code == 401:
            raise ApiError("Unauthorized: Invalid access token")
        elif response.status_code == 403:
            raise ApiError("Forbidden: Insufficient permissions")
        elif response.status_code == 404:
            raise ApiError("Not Found: Resource does not exist")
        elif response.status_code == 422:
            error_data = response.json() if response.content else {}
            errors = error_data.get("errors", {})
            raise ApiError(f"Validation Error: {errors}")
        elif response.status_code >= 400:
            raise ApiError(f"HTTP {response.status_code}: {response.reason}")
        
        # Return JSON response or empty dict for successful requests with no content
        return response.json() if response.content else {}
        
    except requests.RequestException as e:
        raise ApiError(f"Request failed: {str(e)}")
    except json.JSONDecodeError:
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
    id: int = Field(description="Event ID")
    uid: Optional[str] = Field(description="Event unique identifier", default=None)
    project_id: int = Field(description="Associated project ID")
    user_id: int = Field(description="User who logged the time")
    day: str = Field(description="Date of the event (YYYY-MM-DD)")
    from_time: Optional[str] = Field(description="Start time (HH:MM)", default=None)
    to_time: Optional[str] = Field(description="End time (HH:MM)", default=None)
    duration: Optional[int] = Field(description="Duration in minutes", default=None)
    note: Optional[str] = Field(description="Event description/note", default=None)
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


class Webhook(BaseModel):
    """Webhook information structure"""
    id: int = Field(description="Webhook ID")
    target_url: str = Field(description="Webhook target URL")
    event: str = Field(description="Event type that triggers webhook")
    created_at: Optional[Union[str, int]] = Field(description="Webhook creation timestamp", default=None)
    updated_at: Optional[Union[str, int]] = Field(description="Webhook last update timestamp", default=None)


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


class UserList(BaseModel):
    """List of users"""
    users: List[User] = Field(description="List of user objects")


class EventList(BaseModel):
    """List of events"""
    events: List[Event] = Field(description="List of event objects")


class TeamList(BaseModel):
    """List of teams"""
    teams: List[Team] = Field(description="List of team objects")


class LabelList(BaseModel):
    """List of labels"""
    labels: List[Label] = Field(description="List of label objects")


class ForecastList(BaseModel):
    """List of forecasts"""
    forecasts: List[Forecast] = Field(description="List of forecast objects")


class WebhookList(BaseModel):
    """List of webhooks"""
    webhooks: List[Webhook] = Field(description="List of webhook objects")


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
            # Use model_validate with from_attributes to handle extra fields gracefully
            try:
                account = Account.model_validate(account_data)
                accounts.append(account)
            except Exception as e:
                # If validation fails, create a minimal account object
                accounts.append(Account(
                    id=account_data.get("id", 0),
                    name=account_data.get("name", "Unknown"),
                    created_at=account_data.get("created_at"),
                    updated_at=account_data.get("updated_at")
                ))
        
        return AccountList(accounts=accounts)
    except ApiError as e:
        raise Exception(f"Failed to list accounts: {str(e)}")


@mcp.tool()
def get_account(account_id: int) -> Account:
    """Retrieve a specific account by ID"""
    try:
        response = make_request("GET", f"/accounts/{account_id}")
        return Account(**response)
    except ApiError as e:
        raise Exception(f"Failed to get account {account_id}: {str(e)}")


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
        clients = [Client(**client) for client in response]
        return ClientList(clients=clients)
    except ApiError as e:
        raise Exception(f"Failed to list clients: {str(e)}")


@mcp.tool()
def get_client(account_id: int, client_id: int) -> Client:
    """Retrieve a specific client by ID"""
    try:
        response = make_request("GET", f"/{account_id}/clients/{client_id}")
        return Client(**response)
    except ApiError as e:
        raise Exception(f"Failed to get client {client_id}: {str(e)}")


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
        response = make_request("POST", f"/{account_id}/clients", data=data)
        return Client(**response)
    except ApiError as e:
        raise Exception(f"Failed to create client: {str(e)}")


@mcp.tool()
def update_client(account_id: int, client_id: int, name: Optional[str] = None, active: Optional[bool] = None) -> Client:
    """Update an existing client"""
    try:
        data = {"client": {}}
        if name is not None:
            data["client"]["name"] = name
        if active is not None:
            data["client"]["active"] = active
            
        response = make_request("PUT", f"/{account_id}/clients/{client_id}", data=data)
        return Client(**response)
    except ApiError as e:
        raise Exception(f"Failed to update client {client_id}: {str(e)}")


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
        projects = [Project(**project) for project in response]
        return ProjectList(projects=projects)
    except ApiError as e:
        raise Exception(f"Failed to list projects: {str(e)}")


@mcp.tool()
def get_project(account_id: int, project_id: int) -> Project:
    """Retrieve a specific project by ID"""
    try:
        response = make_request("GET", f"/{account_id}/projects/{project_id}")
        return Project(**response)
    except ApiError as e:
        raise Exception(f"Failed to get project {project_id}: {str(e)}")


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
            
        response = make_request("POST", f"/{account_id}/projects", data=data)
        return Project(**response)
    except ApiError as e:
        raise Exception(f"Failed to create project: {str(e)}")


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
            
        response = make_request("PUT", f"/{account_id}/projects/{project_id}", data=data)
        return Project(**response)
    except ApiError as e:
        raise Exception(f"Failed to update project {project_id}: {str(e)}")


@mcp.tool()
def delete_project(account_id: int, project_id: int) -> dict[str, str]:
    """Delete a project"""
    try:
        make_request("DELETE", f"/{account_id}/projects/{project_id}")
        return {"result": f"Project {project_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete project {project_id}: {str(e)}")


# ============================================================================
# USER TOOLS
# ============================================================================

@mcp.tool()
def list_users(account_id: int) -> UserList:
    """List all users for an account"""
    try:
        response = make_request("GET", f"/{account_id}/users")
        users = [User(**user) for user in response]
        return UserList(users=users)
    except ApiError as e:
        raise Exception(f"Failed to list users: {str(e)}")


@mcp.tool()
def get_user(account_id: int, user_id: int) -> User:
    """Retrieve a specific user by ID"""
    try:
        response = make_request("GET", f"/{account_id}/users/{user_id}")
        return User(**response)
    except ApiError as e:
        raise Exception(f"Failed to get user {user_id}: {str(e)}")


@mcp.tool()
def get_current_user(account_id: int) -> User:
    """Retrieve the current authenticated user"""
    try:
        response = make_request("GET", f"/{account_id}/users/current")
        return User(**response)
    except ApiError as e:
        raise Exception(f"Failed to get current user: {str(e)}")


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
        response = make_request("POST", f"/{account_id}/users", data=data)
        return User(**response)
    except ApiError as e:
        raise Exception(f"Failed to create user: {str(e)}")


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
            
        response = make_request("PUT", f"/{account_id}/users/{user_id}", data=data)
        return User(**response)
    except ApiError as e:
        raise Exception(f"Failed to update user {user_id}: {str(e)}")


@mcp.tool()
def delete_user(account_id: int, user_id: int) -> dict[str, str]:
    """Delete a user"""
    try:
        make_request("DELETE", f"/{account_id}/users/{user_id}")
        return {"result": f"User {user_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete user {user_id}: {str(e)}")


# ============================================================================
# EVENT TOOLS
# ============================================================================

@mcp.tool()
def list_events(account_id: int, since: Optional[str] = None, upto: Optional[str] = None, user_id: Optional[int] = None, project_id: Optional[int] = None) -> EventList:
    """List all events (time entries) with optional filters"""
    try:
        params = {}
        if since:
            params["since"] = since
        if upto:
            params["upto"] = upto
        if user_id:
            params["user_id"] = user_id
        if project_id:
            params["project_id"] = project_id
            
        response = make_request("GET", f"/{account_id}/events", params=params)
        events = [Event(**event) for event in response]
        return EventList(events=events)
    except ApiError as e:
        raise Exception(f"Failed to list events: {str(e)}")


@mcp.tool()
def get_event(account_id: int, event_id: int) -> Event:
    """Retrieve a specific event by ID"""
    try:
        response = make_request("GET", f"/{account_id}/events/{event_id}")
        return Event(**response)
    except ApiError as e:
        raise Exception(f"Failed to get event {event_id}: {str(e)}")


@mcp.tool()
def create_event(account_id: int, day: str, from_time: str, to_time: str, note: Optional[str] = None, project_id: Optional[int] = None, user_id: Optional[int] = None) -> Event:
    """Create a new event (time entry)"""
    try:
        data = {
            "event": {
                "day": day,
                "from": from_time,
                "to": to_time
            }
        }
        if note:
            data["event"]["note"] = note
        if project_id:
            data["event"]["project_id"] = project_id
        if user_id:
            data["event"]["user_id"] = user_id
            
        endpoint = f"/{account_id}/events"
        if project_id:
            endpoint = f"/{account_id}/projects/{project_id}/events"
            
        response = make_request("POST", endpoint, data=data)
        return Event(**response)
    except ApiError as e:
        raise Exception(f"Failed to create event: {str(e)}")


@mcp.tool()
def update_event(account_id: int, event_id: int, day: Optional[str] = None, from_time: Optional[str] = None, to_time: Optional[str] = None, note: Optional[str] = None) -> Event:
    """Update an existing event"""
    try:
        data = {"event": {}}
        if day is not None:
            data["event"]["day"] = day
        if from_time is not None:
            data["event"]["from"] = from_time
        if to_time is not None:
            data["event"]["to"] = to_time
        if note is not None:
            data["event"]["note"] = note
            
        response = make_request("PUT", f"/{account_id}/events/{event_id}", data=data)
        return Event(**response)
    except ApiError as e:
        raise Exception(f"Failed to update event {event_id}: {str(e)}")


@mcp.tool()
def delete_event(account_id: int, event_id: int) -> dict[str, str]:
    """Delete an event"""
    try:
        make_request("DELETE", f"/{account_id}/events/{event_id}")
        return {"result": f"Event {event_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete event {event_id}: {str(e)}")


@mcp.tool()
def start_timer(account_id: int, event_id: int) -> Event:
    """Start timer on an event"""
    try:
        response = make_request("PUT", f"/{account_id}/events/{event_id}/start")
        return Event(**response)
    except ApiError as e:
        raise Exception(f"Failed to start timer for event {event_id}: {str(e)}")


@mcp.tool()
def stop_timer(account_id: int, event_id: int) -> Event:
    """Stop timer on an event"""
    try:
        response = make_request("PUT", f"/{account_id}/events/{event_id}/stop")
        return Event(**response)
    except ApiError as e:
        raise Exception(f"Failed to stop timer for event {event_id}: {str(e)}")


# ============================================================================
# TEAM TOOLS
# ============================================================================

@mcp.tool()
def list_teams(account_id: int) -> TeamList:
    """List all teams for an account"""
    try:
        response = make_request("GET", f"/{account_id}/teams")
        teams = [Team(**team) for team in response]
        return TeamList(teams=teams)
    except ApiError as e:
        raise Exception(f"Failed to list teams: {str(e)}")


@mcp.tool()
def get_team(account_id: int, team_id: int) -> Team:
    """Retrieve a specific team by ID"""
    try:
        response = make_request("GET", f"/{account_id}/teams/{team_id}")
        return Team(**response)
    except ApiError as e:
        raise Exception(f"Failed to get team {team_id}: {str(e)}")


@mcp.tool()
def create_team(account_id: int, name: str) -> Team:
    """Create a new team"""
    try:
        data = {
            "team": {
                "name": name
            }
        }
        response = make_request("POST", f"/{account_id}/teams", data=data)
        return Team(**response)
    except ApiError as e:
        raise Exception(f"Failed to create team: {str(e)}")


@mcp.tool()
def update_team(account_id: int, team_id: int, name: str) -> Team:
    """Update an existing team"""
    try:
        data = {
            "team": {
                "name": name
            }
        }
        response = make_request("PUT", f"/{account_id}/teams/{team_id}", data=data)
        return Team(**response)
    except ApiError as e:
        raise Exception(f"Failed to update team {team_id}: {str(e)}")


@mcp.tool()
def delete_team(account_id: int, team_id: int) -> dict[str, str]:
    """Delete a team"""
    try:
        make_request("DELETE", f"/{account_id}/teams/{team_id}")
        return {"result": f"Team {team_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete team {team_id}: {str(e)}")


# ============================================================================
# LABEL TOOLS
# ============================================================================

@mcp.tool()
def list_labels(account_id: int) -> LabelList:
    """List all labels/tags for an account"""
    try:
        response = make_request("GET", f"/{account_id}/labels")
        labels = [Label(**label) for label in response]
        return LabelList(labels=labels)
    except ApiError as e:
        raise Exception(f"Failed to list labels: {str(e)}")


@mcp.tool()
def get_label(account_id: int, label_id: int) -> Label:
    """Retrieve a specific label by ID"""
    try:
        response = make_request("GET", f"/{account_id}/labels/{label_id}")
        return Label(**response)
    except ApiError as e:
        raise Exception(f"Failed to get label {label_id}: {str(e)}")


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
            
        response = make_request("POST", f"/{account_id}/labels", data=data)
        return Label(**response)
    except ApiError as e:
        raise Exception(f"Failed to create label: {str(e)}")


@mcp.tool()
def update_label(account_id: int, label_id: int, name: Optional[str] = None, color: Optional[str] = None) -> Label:
    """Update an existing label"""
    try:
        data = {"label": {}}
        if name is not None:
            data["label"]["name"] = name
        if color is not None:
            data["label"]["color"] = color
            
        response = make_request("PUT", f"/{account_id}/labels/{label_id}", data=data)
        return Label(**response)
    except ApiError as e:
        raise Exception(f"Failed to update label {label_id}: {str(e)}")


@mcp.tool()
def delete_label(account_id: int, label_id: int) -> dict[str, str]:
    """Delete a label"""
    try:
        make_request("DELETE", f"/{account_id}/labels/{label_id}")
        return {"result": f"Label {label_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete label {label_id}: {str(e)}")


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
        forecasts = [Forecast(**forecast) for forecast in response]
        return ForecastList(forecasts=forecasts)
    except ApiError as e:
        raise Exception(f"Failed to list forecasts: {str(e)}")


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
            
        response = make_request("POST", f"/{account_id}/forecasts", data=data)
        return Forecast(**response)
    except ApiError as e:
        raise Exception(f"Failed to create forecast: {str(e)}")


@mcp.tool()
def update_forecast(account_id: int, forecast_id: int, duration: Optional[int] = None, note: Optional[str] = None) -> Forecast:
    """Update an existing forecast"""
    try:
        data = {"forecast": {}}
        if duration is not None:
            data["forecast"]["duration"] = duration
        if note is not None:
            data["forecast"]["note"] = note
            
        response = make_request("PUT", f"/{account_id}/forecasts/{forecast_id}", data=data)
        return Forecast(**response)
    except ApiError as e:
        raise Exception(f"Failed to update forecast {forecast_id}: {str(e)}")


@mcp.tool()
def delete_forecast(account_id: int, forecast_id: int) -> dict[str, str]:
    """Delete a forecast"""
    try:
        make_request("DELETE", f"/{account_id}/forecasts/{forecast_id}")
        return {"result": f"Forecast {forecast_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete forecast {forecast_id}: {str(e)}")


# ============================================================================
# WEBHOOK TOOLS
# ============================================================================

@mcp.tool()
def list_webhooks(account_id: int) -> WebhookList:
    """List all webhooks for an account"""
    try:
        response = make_request("GET", f"/{account_id}/webhooks")
        webhooks = [Webhook(**webhook) for webhook in response]
        return WebhookList(webhooks=webhooks)
    except ApiError as e:
        raise Exception(f"Failed to list webhooks: {str(e)}")


@mcp.tool()
def get_webhook(account_id: int, webhook_id: int) -> Webhook:
    """Retrieve a specific webhook by ID"""
    try:
        response = make_request("GET", f"/{account_id}/webhooks/{webhook_id}")
        return Webhook(**response)
    except ApiError as e:
        raise Exception(f"Failed to get webhook {webhook_id}: {str(e)}")


@mcp.tool()
def create_webhook(account_id: int, target_url: str, event: str) -> Webhook:
    """Create a new webhook"""
    try:
        data = {
            "webhook": {
                "target_url": target_url,
                "event": event
            }
        }
        response = make_request("POST", f"/{account_id}/webhooks", data=data)
        return Webhook(**response)
    except ApiError as e:
        raise Exception(f"Failed to create webhook: {str(e)}")


@mcp.tool()
def update_webhook(account_id: int, webhook_id: int, target_url: Optional[str] = None, event: Optional[str] = None) -> Webhook:
    """Update an existing webhook"""
    try:
        data = {"webhook": {}}
        if target_url is not None:
            data["webhook"]["target_url"] = target_url
        if event is not None:
            data["webhook"]["event"] = event
            
        response = make_request("PUT", f"/{account_id}/webhooks/{webhook_id}", data=data)
        return Webhook(**response)
    except ApiError as e:
        raise Exception(f"Failed to update webhook {webhook_id}: {str(e)}")


@mcp.tool()
def delete_webhook(account_id: int, webhook_id: int) -> dict[str, str]:
    """Delete a webhook"""
    try:
        make_request("DELETE", f"/{account_id}/webhooks/{webhook_id}")
        return {"result": f"Webhook {webhook_id} deleted successfully"}
    except ApiError as e:
        raise Exception(f"Failed to delete webhook {webhook_id}: {str(e)}")


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
        raise Exception(f"Failed to get reports: {str(e)}")


# ============================================================================
# UTILITY TOOLS
# ============================================================================

@mcp.tool()
def get_permissions(account_id: int, user_id: Optional[int] = None) -> dict[str, Any]:
    """Get user permissions for an account"""
    try:
        if user_id:
            endpoint = f"/{account_id}/users/{user_id}/permissions"
        else:
            endpoint = f"/{account_id}/permissions"
            
        response = make_request("GET", endpoint)
        return {"permissions": response}
    except ApiError as e:
        raise Exception(f"Failed to get permissions: {str(e)}")


@mcp.tool()
def list_roles(account_id: int) -> dict[str, Any]:
    """List available roles for an account"""
    try:
        response = make_request("GET", f"/{account_id}/roles")
        return {"roles": response}
    except ApiError as e:
        raise Exception(f"Failed to list roles: {str(e)}")


@mcp.tool()
def get_user_capacities(account_id: int, user_id: Optional[int] = None, since: Optional[str] = None, upto: Optional[str] = None) -> dict[str, Any]:
    """Get user capacity information"""
    try:
        params = {}
        if since:
            params["since"] = since
        if upto:
            params["upto"] = upto
            
        if user_id:
            endpoint = f"/{account_id}/users/{user_id}/capacities"
        else:
            endpoint = f"/{account_id}/capacities"
            
        response = make_request("GET", endpoint, params=params)
        return {"capacities": response}
    except ApiError as e:
        raise Exception(f"Failed to get user capacities: {str(e)}")


def main():
    """Main function to run the MCP server"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()