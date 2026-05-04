# API Overview

This document tracks the current code structure, key APIs, database mappings, and schema entrypoints for the SIEM platform.

## Base API Conventions
- Main API prefix: `/api/v1/`
- Authentication: DRF Token Authentication
- Time zone: UTC (`USE_TZ = True`, `TIME_ZONE = UTC`)
- Response style: standard DRF JSON responses
- Pagination/filtering behavior depends on the individual viewset or view implementation

## Schema / API Discovery
- Django admin: `/admin/`
- Current route discovery is primarily through Django router/viewset URL registration

## Backend Apps
- `accounts`: Authentication, user management, group management, RBAC helpers
- `alerts`: Alert list, dashboard metrics, sync, and connector config helpers
- `cmdb`: Asset inventory, asset columns, and audit logs
- `correlation`: Correlation policy and correlation event endpoints
- `integrations`: Integration metadata and external connection utilities
- `dashboards`: Dashboard metadata CRUD
- `tickets`: SLA-aware ticket CRUD, lifecycle actions, and SLA metrics
- `workflows`: Workflow definitions, steps, executions, saved nodes, and workflow stats
- `workflow_interfaces`: External interface endpoints and ingest/webhook entrypoints
- `ai_assistant`: Chat, MCP protocol, skill registry/configuration, and external MCP server management
- `orchestrator`: Tasks, task runs, and task request logs
- `siem_project`: Project settings and top-level URL routing

## Frontend Code Map
| Area | Path | Purpose |
| --- | --- | --- |
| Project URL map | `backend/siem_project/urls.py` | Top-level API routing and app inclusion |
| Auth routes | `backend/accounts/urls.py` | Login/logout/register, users, groups, RBAC |
| Alerts routes | `backend/alerts/urls.py` | Alert list, dashboard, sync, config, diagnostics |
| Tickets routes | `backend/tickets/urls.py` | Ticket CRUD and SLA endpoints |
| CMDB routes | `backend/cmdb/urls.py` | Asset, column, and audit log APIs |
| Correlation routes | `backend/correlation/urls.py` | Correlation policy and events |
| Workflows routes | `backend/workflows/urls.py` | Workflow CRUD, execution, stats, and nodes |
| Interface routes | `backend/workflow_interfaces/urls.py` | Endpoint registration and ingest/webhook ingress |

## API & Key Endpoints

### Authentication
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Auth | `/api/v1/auth/login/` | POST | User login | `backend/accounts/views.py` |
| Auth | `/api/v1/auth/logout/` | POST | User logout | `backend/accounts/views.py` |
| Auth | `/api/v1/auth/register/` | POST | User registration | `backend/accounts/views.py` |
| Auth | `/api/v1/accounts/change-password/` | POST | Change current password | `backend/accounts/urls.py` |

### Accounts / RBAC
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Users | `/api/v1/accounts/users/` | GET/POST | List or create users | `backend/accounts/urls.py` |
| Users | `/api/v1/accounts/users/{id}/` | GET/PUT/PATCH/DELETE | User CRUD | `backend/accounts/urls.py` |
| Groups | `/api/v1/accounts/groups/` | GET/POST | List or create groups | `backend/accounts/urls.py` |
| Groups | `/api/v1/accounts/groups/{id}/` | GET/PUT/PATCH/DELETE | Group CRUD | `backend/accounts/urls.py` |
| RBAC | `/api/v1/accounts/rbac/` | various | RBAC helper routes | `backend/accounts/urls.py` |
| Permissions | `/api/v1/permissions/` | various | Permission-related routes | `backend/siem_project/urls.py` |

### Alerts
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Alerts list | `/api/v1/alerts/list/` | GET | Paginated alert list | `backend/alerts/urls.py` |
| Alerts dashboard | `/api/v1/alerts/dashboard/` | GET | Dashboard metrics and trend data | `backend/alerts/urls.py` |
| Alerts sync | `/api/v1/alerts/sync/` | POST | Trigger alert synchronization | `backend/alerts/urls.py` |
| ES config | `/api/v1/alerts/config/es/` | GET/POST | Elasticsearch connection settings | `backend/alerts/urls.py` |
| Webhook config | `/api/v1/alerts/config/webhook/` | GET/POST | Webhook configuration | `backend/alerts/urls.py` |
| ES diagnostics | `/api/v1/alerts/debug/es_status/` | GET | Elasticsearch status diagnostics | `backend/alerts/urls.py` |

### Tickets
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Tickets | `/api/v1/tickets/` | GET/POST | List or create tickets | `backend/tickets/urls.py` |
| Ticket detail | `/api/v1/tickets/{ticket_number}/` | GET/PUT/PATCH/DELETE | Ticket CRUD by ticket number | `backend/tickets/urls.py` |
| SLA metrics | `/api/v1/tickets/sla/` | GET | SLA metric records | `backend/tickets/urls.py` |
| Ticket choices | `/api/v1/tickets/field_choices/` | GET | Field dropdown values | `backend/tickets/views.py` |
| Status update | `/api/v1/tickets/{ticket_number}/update_status/` | POST | Update ticket status | `backend/tickets/views.py` |
| Resolve | `/api/v1/tickets/{ticket_number}/resolve/` | POST | Resolve a ticket | `backend/tickets/views.py` |

### CMDB
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Assets | `/api/v1/cmdb/assets/` | GET/POST | Asset inventory CRUD | `backend/cmdb/urls.py` |
| Asset detail | `/api/v1/cmdb/assets/{id}/` | GET/PUT/PATCH/DELETE | Asset CRUD | `backend/cmdb/urls.py` |
| Columns | `/api/v1/cmdb/columns/` | GET/POST | Asset column metadata | `backend/cmdb/urls.py` |
| Column detail | `/api/v1/cmdb/columns/{id}/` | GET/PUT/PATCH/DELETE | Asset column CRUD | `backend/cmdb/urls.py` |
| Logs | `/api/v1/cmdb/logs/` | GET | Asset audit logs | `backend/cmdb/urls.py` |
| Log detail | `/api/v1/cmdb/logs/{id}/` | GET/PUT/PATCH/DELETE | Audit log CRUD | `backend/cmdb/urls.py` |

### Correlation
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Policy | `/api/v1/correlation/policy/` | GET/POST | Fetch or update correlation policy | `backend/correlation/urls.py` |
| Events | `/api/v1/correlation/events/` | GET | Correlation event stream / mock series | `backend/correlation/urls.py` |

### Dashboards / Integrations / Orchestrator
These routes are registered in `backend/siem_project/urls.py` via DRF routers, so the exact set of list/detail actions depends on each ViewSet implementation.

| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Dashboards | `/api/v1/dashboards/` | GET/POST | Dashboard metadata CRUD | `backend/dashboards/views.py` |
| Integrations | `/api/v1/integrations/` | GET/POST | Integration metadata CRUD | `backend/integrations/views.py` |
| Tasks | `/api/v1/tasks/` | GET/POST | Task definitions | `backend/orchestrator/views.py` |
| Task runs | `/api/v1/task_runs/` | GET/POST | Task run records | `backend/orchestrator/views.py` |
| Task request logs | `/api/v1/task_request_logs/` | GET/POST | Task request logs | `backend/orchestrator/views.py` |

### Special Integration Utilities
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| ES test | `/api/v1/integrations/test_es` | GET/POST | Test Elasticsearch connectivity | `backend/siem_project/urls.py` |
| ES preview | `/api/v1/integrations/preview_es` | GET/POST | Preview ES index data | `backend/siem_project/urls.py` |
| ES mapping preview | `/api/v1/integrations/preview_es_mapping` | GET/POST | Preview ES field mappings | `backend/siem_project/urls.py` |

### Workflow Engine
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Action templates | `/api/v1/workflows/action-templates/` | GET/POST | Action template CRUD | `backend/workflows/urls.py` |
| Workflows | `/api/v1/workflows/workflows/` | GET/POST | Workflow CRUD | `backend/workflows/urls.py` |
| Executions | `/api/v1/workflows/executions/` | GET/POST | Workflow execution records | `backend/workflows/urls.py` |
| Steps | `/api/v1/workflows/steps/` | GET/POST | Workflow step CRUD | `backend/workflows/urls.py` |
| Saved nodes | `/api/v1/workflows/saved-nodes/` | GET/POST | Persisted node layouts | `backend/workflows/urls.py` |
| Workflow stats | `/api/v1/workflows/stats/` | GET | Workflow statistics | `backend/workflows/urls.py` |

### Workflow Interfaces
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| Interface endpoints | `/api/v1/interfaces/endpoints/` | GET/POST | External endpoint registry | `backend/workflow_interfaces/urls.py` |
| Interface ingest | `/api/v1/interfaces/endpoints/{endpoint_id}/ingest/` | POST | Ingest data for a registered endpoint | `backend/workflow_interfaces/urls.py` |
| Webhook ingest | `/api/v1/interfaces/webhooks/{endpoint_id}/` | POST | Webhook-style ingest endpoint | `backend/workflow_interfaces/urls.py` |

### AI Assistant / MCP
| Area | Endpoint | Method | Purpose | Backend ref |
| --- | --- | --- | --- | --- |
| AI connectivity | `/api/v1/ai-assistant/test-connectivity` | GET/POST | Health/connectivity check | `backend/siem_project/urls.py` |
| MCP monitor | `/api/v1/ai-assistant/mcp-monitor` | GET/POST | MCP monitoring | `backend/siem_project/urls.py` |
| MCP registry | `/api/v1/ai-assistant/mcp-registry/servers` | GET/POST | MCP server registry | `backend/siem_project/urls.py` |
| Skills monitor | `/api/v1/ai-assistant/skill-monitor` | GET/POST | Skill monitoring | `backend/siem_project/urls.py` |
| Skills catalog | `/api/v1/ai-assistant/skills/catalog` | GET/POST | Available skills catalog | `backend/siem_project/urls.py` |
| Skills config | `/api/v1/ai-assistant/skills/config` | GET/POST | Skill config collection | `backend/siem_project/urls.py` |
| Skill config detail | `/api/v1/ai-assistant/skills/config/{name}` | GET/PUT/PATCH/DELETE | One skill config | `backend/siem_project/urls.py` |
| Skill content detail | `/api/v1/ai-assistant/skills/content/{name}` | GET | Skill content payload | `backend/siem_project/urls.py` |
| AI chat | `/api/v1/ai-assistant/chat` | POST | Chat interaction endpoint | `backend/siem_project/urls.py` |
| External MCP | `/api/v1/ai-assistant/external-mcp` | GET/POST | External MCP server list | `backend/siem_project/urls.py` |
| External MCP detail | `/api/v1/ai-assistant/external-mcp/{name}` | GET/PUT/PATCH/DELETE | One external MCP server | `backend/siem_project/urls.py` |
| External MCP start | `/api/v1/ai-assistant/external-mcp/{name}/start` | POST | Start external MCP server | `backend/siem_project/urls.py` |
| External MCP stop | `/api/v1/ai-assistant/external-mcp/{name}/stop` | POST | Stop external MCP server | `backend/siem_project/urls.py` |
| MCP JSON-RPC | `/api/v1/mcp` | POST | Standard MCP JSON-RPC endpoint | `backend/siem_project/urls.py` |
| MCP tools | `/api/v1/mcp/tools` | GET | Tool manifest | `backend/siem_project/urls.py` |
| MCP ticket context | `/api/v1/mcp/ticket-context/{ticket_number}` | GET | Ticket context retrieval | `backend/siem_project/urls.py` |
| MCP similar cases | `/api/v1/mcp/ticket-search/similar-cases` | GET/POST | Find similar tickets | `backend/siem_project/urls.py` |
| MCP CMDB lookup | `/api/v1/mcp/cmdb/asset-lookup` | GET/POST | Asset lookup | `backend/siem_project/urls.py` |
| MCP observables extract | `/api/v1/mcp/observables/extract` | POST | Extract observables | `backend/siem_project/urls.py` |

