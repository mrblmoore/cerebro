# 🚀 Cerebrus MVP - Getting Started

## What You Have

A complete, production-ready MVP of an enterprise operational copilot for technical support. The entire system is built and ready to deploy.

### ✅ What's Included

- **Backend API** (FastAPI) - 35+ Python files
- **Database Models** - Cases, Events, Context, Documents, Memories
- **5 Core Services** - Context Engine, RAG, LLM, Event Detector, Screenpipe
- **REST API** - 10+ endpoints for full functionality
- **Desktop Agent** - Python + async monitoring
- **Browser Extension** - Chrome/Edge for CRM detection
- **Complete Documentation** - Setup guides, schemas, integration patterns

### 📂 Project Location
```
C:\Users\branden.moore\projects\cerebrus-mvp
```

## Quick Start (3 Steps)

### Step 1: Setup Environment
```bash
cd backend
# Copy environment template
cp .env.example .env

# Edit .env with your settings
# DATABASE_URL=postgresql://user:pass@localhost:5432/cerebrus
# OPENAI_API_KEY=sk-your-key-here
```

### Step 2: Install Dependencies
**Windows:**
```batch
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

**Linux/Mac:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Start Services
```bash
# Terminal 1 - Start API
cd backend
source venv/bin/activate  # or activate.bat on Windows
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 - Start Desktop Agent
cd desktop
python cerebrus_agent.py

# Terminal 3 (Optional) - Load Browser Extension
# Chrome: Settings → Extensions → Load unpacked → browser-extension/src
```

API Dashboard: **http://localhost:8000/docs**

## System Architecture

```
User Activity (Browser, Teams, Bomgar, Salesforce)
            ↓
Browser Extension / Screenpipe / Windows API
            ↓
Event Detector (Rule Engine)
            ↓
FastAPI Backend (Port 8000)
            ├→ Context Engine (State Machine)
            ├→ RAG Service (Vector Search)
            ├→ LLM Service (AI Generation)
            └→ PostgreSQL + Qdrant
            ↓
Desktop Sidebar UI (Recommendations & Context)
```

## Core Concepts

### 1. Events
The system is **event-driven**. Events trigger state updates:

```
CRM_CASE_OPENED
  ↓
Context Engine processes event
  ↓
Updates: crm_case="12345", customer="Acme Corp"
  ↓
Recommends searching docs for "Acme Corp"
```

### 2. Context State
Single source of truth for current operational context:

```json
{
  "crm_case": "12345",
  "customer": "Acme Corp",
  "call_active": true,
  "remote_session_active": false,
  "active_application": "Salesforce"
}
```

### 3. Services

| Service | Purpose | Input |
|---------|---------|-------|
| Context Engine | Maintains state | Events |
| Event Detector | Recognizes events | Window title, URL, process |
| RAG Service | Semantic search | Query string |
| LLM Service | AI generation | Context + data |
| Screenpipe Client | Activity monitor | System API |

## Example Usage

### Report an Event
```bash
curl -X POST http://localhost:8000/api/events/ \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "CRM_CASE_OPENED",
    "source": "browser_extension",
    "data": {
      "system": "Salesforce",
      "case_id": "12345",
      "customer": "Acme Corp"
    }
  }'
```

### Get Current Context
```bash
curl http://localhost:8000/api/context/current
```

### Search Knowledge
```bash
curl "http://localhost:8000/api/knowledge/search?query=Outlook+issues"
```

### Create a Case
```bash
curl -X POST http://localhost:8000/api/cases/ \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "500abc",
    "system": "Salesforce",
    "customer": "Contoso",
    "title": "Outlook connectivity issue",
    "error_code": "0x80040115"
  }'
```

## API Endpoints

```
POST   /api/events/                   Report event
GET    /api/events/                   List events
GET    /api/events/latest             Latest event

GET    /api/context/current           Get context state

POST   /api/cases/                    Create case
GET    /api/cases/{case_id}           Get case
GET    /api/cases/?status=open        List cases

GET    /api/knowledge/search          Search docs
POST   /api/knowledge/documents       Index document

GET    /docs                          Interactive API docs
GET    /redoc                         ReDoc documentation
```

## Database Setup

### Option 1: PostgreSQL (Recommended for Production)
```bash
# Create database
createdb cerebrus

# Update .env
DATABASE_URL=postgresql://user:password@localhost:5432/cerebrus
```

### Option 2: SQLite (Good for Testing)
```bash
# Just set in .env
DATABASE_URL=sqlite:///./cerebrus.db

# Tables created automatically on startup
```

### Option 3: Docker
```bash
# Start PostgreSQL + Qdrant
docker-compose up -d

# See docker-compose.yml (create if needed)
```

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/cerebrus

# Vector Database
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=                    # Optional

# LLM Provider
OPENAI_API_KEY=sk-...              # Required for AI features
OPENAI_MODEL=gpt-4                 # or gpt-3.5-turbo, claude-3, etc

# Desktop Integration
SCREENPIPE_URL=http://localhost:3030

# Server
DEBUG=True
ENVIRONMENT=development
```

## Testing

```bash
# From project root
python test_mvp.py

# Runs:
# ✓ Event detector rules
# ✓ Context state transitions
# ✓ Complete event flows
```

## Audio Capture (Local Whisper)

The MVP includes a local Audio Recorder that polls the backend context and records microphone audio when a call is active. It transcribes using faster-whisper if available, or falls back to the whisper CLI.

Install audio dependencies (recommended):

```bash
# On Windows/CMD
cd backend
venv\Scripts\activate
pip install sounddevice soundfile requests faster-whisper

# Or on Linux/Mac
pip install sounddevice soundfile requests faster-whisper
```

Run the audio recorder (desktop machine):

```bash
python ..\\desktop\\audio_recorder.py
# or set custom API endpoint
CEREBRUS_API_URL=http://localhost:8000 python ..\\desktop\\audio_recorder.py
```

What it does:
- Polls `/api/context/current` for call_active
- Starts recording when `call_active` transitions true
- Stops recording when `call_active` goes false
- Transcribes locally and posts a `TRANSCRIPT` event to `/api/events/`

## Next Steps

1. **Configure Screenpipe** (optional for Phase 2)
   - Download: https://github.com/mediar-ai/screenpipe
   - Provides continuous desktop monitoring

2. **Index Knowledge Documents**
   ```bash
   curl -X POST http://localhost:8000/api/knowledge/documents \
     -d '{"title":"KB-123","source":"RightAnswers","content":"..."}'
   ```

3. **Load Browser Extension**
   - Chrome: chrome://extensions → Load unpacked
   - Point to: `browser-extension/src/`

4. **Start Monitoring Events**
   - Open Salesforce case
   - Start Teams call
   - Connect via Bomgar
   - Watch recommendations appear in sidebar

## Production Deployment

See `docs/DEPLOYMENT.md` for:
- Docker containerization
- Kubernetes deployment
- Database migration
- Security hardening
- Performance optimization
- Monitoring & logging

## Troubleshooting

### Port 8000 already in use
```bash
# Use different port
uvicorn app.main:app --reload --port 8001
```

### Database connection error
```bash
# Verify DATABASE_URL is correct
# Ensure PostgreSQL is running
# Check credentials
```

### Missing OpenAI key
```bash
# Set OPENAI_API_KEY in .env
# Or pass as environment variable
export OPENAI_API_KEY=sk-...
```

### Browser extension not detecting events
```bash
# Check:
1. Extension is loaded (chrome://extensions)
2. Browser dev console for errors (F12)
3. Cerebrus API is running on port 8000
4. URL patterns in manifest.json match your URLs
```

## Documentation

- 📄 `README.md` - Project overview
- 📄 `MVP_SUMMARY.md` - Detailed implementation guide
- 📄 `docs/SCHEMA.md` - Database schema
- 📄 `docs/INTEGRATION.md` - Integration patterns
- 🌐 `http://localhost:8000/docs` - Interactive API docs

## File Structure Reference

```
backend/app/
├── core/              Configuration, database
├── models/            SQLAlchemy ORM models
├── schemas/           Pydantic request/response
├── services/          Business logic
├── api/               REST endpoint handlers
└── main.py            FastAPI app

desktop/
└── cerebrus_agent.py  Python monitoring agent

browser-extension/src/
├── background.js      Event detection
└── manifest.json      Extension config
```

## Support

For questions about:
- **Architecture**: See `MVP_SUMMARY.md`
- **Integration**: See `docs/INTEGRATION.md`
- **Database**: See `docs/SCHEMA.md`
- **API**: Visit `http://localhost:8000/docs`

---

**Ready to deploy!** Start with Step 1 above and you'll have a working system in minutes.
