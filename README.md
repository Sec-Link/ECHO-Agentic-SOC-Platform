# ECHO-SOC-Platform

ECHO-SOC-Platform is an AI-native Security Operations Center (SOC) platform that delivers alert management, correlation, automation, tickets, and CMDB capabilities, with an integrated AI assistant to improve investigation and response efficiency.

## Key Capabilities
- Alert management and correlation (Alerts / Correlation)
- SOC visualization and custom dashboards (Dashboards)
- Ticketing and response closure (Tickets)
- Asset and configuration management (CMDB)
- Automation and workflows (Orchestrator / Workflows / Interfaces)
- Integrations (optional Elasticsearch support)
- AI Assistant and MCP (model collaboration and tool orchestration)

## Tech Stack
- **Backend**: Django 6 + Django REST Framework + PostgreSQL
- **Frontend**: Next.js 15 + React 18 + Ant Design
- **Deployment**: Docker Compose / Kubernetes

## Quick Start (Docker Compose)
### 1) Prepare environment variables
```bash
cp env.example .env
```
Fill in the following variables as needed (see `.env` for examples):
- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`
- `CSRF_TRUSTED_ORIGINS`
- `BACKEND_ORIGIN`
- `ES_HOST` / `ES_USERNAME` / `ES_PASSWORD` (optional)
- `TICKETS_API_BASE` / `TICKETS_API_TOKEN` (optional)

### 2) Start in development mode
```bash
docker-compose -f docker-compose.dev.yml up --build -d
```
Access:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`

### 3) Start in production mode
```bash
docker-compose -f docker-compose.prod.yml up --build -d
```
Production recommendations:
- Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to real domains
- Remove database port exposure
- Use reverse proxy and HTTPS

## Local Development (non-Docker)
> Requires a local PostgreSQL instance.

Backend:
```bash
cd backend
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

## Directory Structure
```
.
├── backend/                # Django backend (API, tasks, orchestration, AI, tickets, etc.)
├── frontend/               # Next.js frontend
├── k8s/                    # Kubernetes manifests
├── docker-compose.dev.yml  # Development compose file
├── docker-compose.prod.yml # Production compose file
├── env.example             # Environment variable template
└── LICENSE.md              # License
```

## API Overview
The backend APIs are prefixed with `/api/v1/`, covering authentication, alerts, correlation, orchestration, tickets, CMDB, and AI assistant modules.

## License
See [LICENSE.md](LICENSE.md). This project uses a modified Apache 2.0 license with additional commercial usage and logo constraints.

## Contributing
Issues and PRs are welcome. Before submitting:
- Provide clear change descriptions
- Do not include sensitive information
- Follow the existing project style
