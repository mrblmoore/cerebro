# Cerebrus MVP

Enterprise-grade personal operational copilot for technical support workflows.

## Architecture

The MVP is structured into three main components:

### Backend (`/backend`)
- **FastAPI** application serving as the core API
- **PostgreSQL** for persistent storage (cases, events, context state)
- **Qdrant** vector database for semantic search
- **Services**: Context Engine, RAG Pipeline, LLM Service, Screenpipe Client

### Desktop Agent (`/desktop`)
- **Python** agent that communicates with the backend
- Monitors for desktop activity and events
- Integrates with Screenpipe for activity tracking
- (Production: Tauri + React for native desktop app)

### Browser Extension (`/browser-extension`)
- Detects Salesforce/CRM pages
- Extracts case IDs and context
- Sends events to backend API

## Quick Start

### 1. Setup Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

### 2. Setup Database

```bash
# Requires PostgreSQL running
# Update DATABASE_URL in .env

cp .env.example .env
# Edit .env with your configuration
```

### 3. Start Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Install Screenpipe

Download and run Screenpipe: https://github.com/mediar-ai/screenpipe

### 5. Start Desktop Agent

```bash
cd desktop
python cerebrus_agent.py
```

## API Endpoints

- `POST /api/events/` - Report a new event
- `GET /api/context/current` - Get current context state
- `POST /api/cases/` - Create a new case
- `GET /api/knowledge/search?query=...` - Search knowledge base

## Event Types

- `CRM_CASE_OPENED` - Salesforce case detected
- `CALL_STARTED` - Teams/Zoom call started
- `CALL_ENDED` - Call ended
- `REMOTE_SESSION_CONNECTED` - Bomgar/RDP session started
- `REMOTE_SESSION_DISCONNECTED` - Remote session ended
- `APPLICATION_CHANGED` - Active window/URL changed

## Context State

The system maintains a real-time context state:

```json
{
  "crm_case": "12345",
  "customer": "Contoso",
  "call_active": true,
  "remote_session_active": false,
  "active_application": "Salesforce",
  "active_url": "https://company.lightning.force.com/..."
}
```

## Phase 2 & 3 Roadmap

- **Phase 2**: Personal memory engine, similar case matching, writing style adaptation
- **Phase 3**: Proactive recommendations, automated CRM updates, workflow execution

## Key Components

### Context Engine
Event-driven state machine that tracks current case, call status, and remote sessions.

### RAG Pipeline
Semantic search over knowledge documents (RightAnswers, SharePoint, etc) using Qdrant vector database.

### LLM Service
Generates case summaries, troubleshooting steps, and recommendations using OpenAI API.

### Event Detector
Rule-based detection of CRM events, call state, and application changes.

## Security Notes

- Screenpipe runs locally; no data sent to external servers
- Browser extension uses local-only API calls
- All context stored in PostgreSQL (local or internal network)
- Implement proper access controls before production deployment
