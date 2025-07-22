# Timely API MCP Server

A comprehensive Model Context Protocol (MCP) server that provides seamless integration with the [Timely](https://timelyapp.com/) time tracking API. This server enables AI assistants to manage time entries, projects, clients, users, and more through natural language interactions.

## 🚀 Features

- **Complete API Coverage**: Access all major Timely API endpoints
- **Nango Authentication**: Secure OAuth integration using Nango
- **Structured Responses**: Type-safe, validated data models
- **Error Handling**: Robust error management with helpful messages
- **Real-time Operations**: Start/stop timers, create entries, manage projects

## 📋 Available Operations

### 🏢 Account & User Management
- List and retrieve accounts
- Manage users (create, update, delete, invite)
- Get current user information
- View user permissions and roles
- Check user capacities and availability

### 👥 Client & Project Management
- Create, update, and delete clients
- Manage projects with full CRUD operations
- Organize work by client relationships
- Set project descriptions and status

### ⏰ Time Tracking
- List, create, update, and delete time entries
- Start and stop timers on events
- Filter entries by date, user, or project
- Bulk operations for multiple entries

### 🏷️ Organization Tools
- Create and manage labels/tags
- Organize teams and team assignments
- Set up forecasts and task planning
- Generate detailed reports

### 🔗 Integration Features
- Webhook management for real-time notifications
- Report generation with custom filters
- Bulk operations for efficiency
- Connection testing and token refresh

## 🛠️ Installation

### Prerequisites
- Python 3.8 or higher
- A Timely account with API access
- Nango setup for OAuth authentication

### Setup Steps

1. **Clone or download the MCP server file**
   ```bash
   # Save the main.py file to your desired directory
   cd your-mcp-directory
   ```

2. **Install required dependencies**
   ```bash
   pip install requests pydantic mcp
   ```

3. **Configure Nango Authentication**
   
   Set up these environment variables with your Nango configuration:
   ```bash
   export NANGO_CONNECTION_ID="your_connection_id"
   export NANGO_INTEGRATION_ID="your_integration_id"
   export NANGO_BASE_URL="https://api.nango.dev"
   export NANGO_SECRET_KEY="your_nango_secret_key"
   ```

4. **Run the MCP server**
   ```bash
   python main.py
   ```

## 🔧 Nango Setup Guide

### 1. Create a Nango Account
- Sign up at [nango.dev](https://nango.dev)
- Create a new project

### 2. Configure Timely Integration
- Add Timely as a provider in your Nango dashboard
- Configure OAuth settings with Timely's endpoints:
  - Authorization URL: `https://api.timelyapp.com/1.1/oauth/authorize`
  - Token URL: `https://api.timelyapp.com/1.1/oauth/token`

### 3. Create a Connection
- Create a connection for your Timely account
- Note down the Connection ID for environment variables

### 4. Get Your Credentials
- Find your Integration ID in the Nango dashboard
- Generate a Secret Key for API access
- Set the base URL (typically `https://api.nango.dev`)

## 💡 Usage Examples

### Basic Time Tracking
```python
# List all accounts
accounts = list_accounts()

# Get current user
user = get_current_user(account_id=123456)

# Create a time entry
event = create_event(
    account_id=123456,
    day="2024-01-15",
    from_time="09:00",
    to_time="17:00",
    note="Working on project documentation"
)

# Start a timer
start_timer(account_id=123456, event_id=789)
```

### Project Management
```python
# Create a new client
client = create_client(
    account_id=123456,
    name="Acme Corporation",
    active=True
)

# Create a project for the client
project = create_project(
    account_id=123456,
    name="Website Redesign",
    description="Complete redesign of corporate website",
    client_id=client.id
)

# List all projects
projects = list_projects(account_id=123456)
```

### Team Collaboration
```python
# Create a team
team = create_team(
    account_id=123456,
    name="Development Team"
)

# Invite a user
user = create_user(
    account_id=123456,
    name="Jane Smith",
    email="jane@company.com",
    user_level="normal"
)

# Create a forecast/task
forecast = create_forecast(
    account_id=123456,
    project_id=project.id,
    user_id=user.id,
    day="2024-01-20",
    duration=480,  # 8 hours in minutes
    note="Frontend development work"
)
```

## 🔍 Available Tools

### Authentication & Testing
- `test_connection()` - Test API connectivity
- `refresh_nango_token()` - Manually refresh authentication token

### Account Management
- `list_accounts()` - Get all accessible accounts
- `get_account(account_id)` - Get specific account details

### User Operations
- `list_users(account_id)` - List all users
- `get_user(account_id, user_id)` - Get user details
- `get_current_user(account_id)` - Get authenticated user info
- `create_user(account_id, name, email, user_level)` - Invite new user
- `update_user(account_id, user_id, ...)` - Update user information
- `delete_user(account_id, user_id)` - Remove user

### Client Management
- `list_clients(account_id)` - List all clients
- `get_client(account_id, client_id)` - Get client details
- `create_client(account_id, name, active)` - Create new client
- `update_client(account_id, client_id, ...)` - Update client
- `delete_client(account_id, client_id)` - Remove client

### Project Management
- `list_projects(account_id)` - List all projects
- `get_project(account_id, project_id)` - Get project details
- `create_project(account_id, name, description, client_id)` - Create project
- `update_project(account_id, project_id, ...)` - Update project
- `delete_project(account_id, project_id)` - Remove project

### Time Tracking
- `list_events(account_id, since, upto, user_id, project_id)` - List time entries
- `get_event(account_id, event_id)` - Get specific entry
- `create_event(account_id, day, from_time, to_time, note, project_id)` - Log time
- `update_event(account_id, event_id, ...)` - Modify entry
- `delete_event(account_id, event_id)` - Remove entry
- `start_timer(account_id, event_id)` - Start timing
- `stop_timer(account_id, event_id)` - Stop timing

### Organization Tools
- `list_teams(account_id)`, `create_team(account_id, name)`, etc. - Team management
- `list_labels(account_id)`, `create_label(account_id, name, color)`, etc. - Label management
- `list_forecasts(account_id)`, `create_forecast(...)`, etc. - Task planning
- `list_webhooks(account_id)`, `create_webhook(...)`, etc. - Webhook management

### Reporting
- `get_reports(account_id, start_date, end_date, user_ids, project_ids)` - Generate reports
- `get_permissions(account_id, user_id)` - View permissions
- `get_user_capacities(account_id, user_id, since, upto)` - Check availability

## 🛡️ Error Handling

The server includes comprehensive error handling for:

- **Authentication Errors**: Invalid tokens, missing Nango configuration
- **API Errors**: HTTP status codes (401, 403, 404, 422, 500)
- **Network Issues**: Timeout, connection failures
- **Validation Errors**: Invalid data formats, missing required fields

Error messages are clear and actionable, helping you quickly identify and resolve issues.

## 📊 Response Format

All tools return structured, typed data using Pydantic models:

```python
# Example Account response
{
    "accounts": [
        {
            "id": 123456,
            "name": "My Company",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T12:30:00Z"
        }
    ]
}

# Example Event response
{
    "id": 789,
    "project_id": 456,
    "user_id": 123,
    "day": "2024-01-15",
    "from_time": "09:00",
    "to_time": "17:00",
    "duration": 480,
    "note": "Development work",
    "created_at": "2024-01-15T09:00:00Z"
}
```

## 🤝 Contributing

Found a bug or want to add a feature? Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📞 Support

- **Timely API Documentation**: [dev.timelyapp.com](https://dev.timelyapp.com)
- **Nango Documentation**: [docs.nango.dev](https://docs.nango.dev)
- **MCP Protocol**: [modelcontextprotocol.io](https://modelcontextprotocol.io)

For issues with this MCP server, please check that:
1. Your Nango configuration is correct
2. Your Timely account has API access
3. All environment variables are properly set
4. You have the required permissions for the operations you're trying to perform

## 📝 License

This project is provided as-is for educational and development purposes. Please ensure compliance with Timely's API terms of service when using this server.

---

**Made with ❤️ for the MCP community**

*Streamline your time tracking workflows with natural language AI interactions!*