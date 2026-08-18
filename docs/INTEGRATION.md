# Cerebrus MVP - Integration Guide

## Event Flow

### 1. Browser Extension → Backend

When user navigates to Salesforce:

```
Browser Extension detects URL
  ↓
Extracts case ID: "500abc123"
  ↓
POST /api/events/
{
  "event_type": "CRM_CASE_OPENED",
  "source": "browser_extension",
  "data": {
    "system": "Salesforce",
    "case_id": "500abc123",
    "customer": "Contoso",
    "url": "https://company.lightning.force.com/..."
  }
}
  ↓
Context Engine processes event
  ↓
Updates ContextState
  ↓
Generates recommendations
```

### 2. Screenpipe → Event Detector → Backend

Screenpipe captures desktop activity (continuously running):

```
Screenpipe detects window change
  ↓
Event Detector applies rules
  ↓
Detects: "Bomgar session connected to SERVER-01"
  ↓
POST /api/events/
{
  "event_type": "REMOTE_SESSION_CONNECTED",
  "source": "screenpipe",
  "data": {"host": "SERVER-01"},
  "screenshot_path": "/tmp/screenpipe_123.png"
}
```

### 3. Teams Integration (Future)

Monitor Teams for call state:

```
Teams call starts
  ↓
POST /api/events/
{
  "event_type": "CALL_STARTED",
  "case_id": "500abc123",
  "source": "teams_integration",
  "data": {
    "application": "Teams",
    "title": "Support Call - Contoso"
  }
}
```

## Context-Aware Recommendations

When context state changes:

```python
# Current state:
{
  "crm_case": "500abc123",
  "customer": "Contoso",
  "call_active": true,
  "remote_session_active": true
}

# System automatically:
1. Searches knowledge base for Contoso issues
2. Retrieves previous similar cases
3. Generates troubleshooting suggestions
4. Prepares draft CRM notes
5. Displays recommendations in sidebar
```

## Example: End-to-End Support Scenario

**09:00** - Support engineer opens Salesforce case #12345

```
Event: CRM_CASE_OPENED
Context: crm_case="12345", customer="Contoso"
Cerebrus Action: Search docs for "Contoso Outlook"
Result: Display 3 related KBs in sidebar
```

**09:03** - Engineer receives Teams call from customer

```
Event: CALL_STARTED
Context: call_active=true
Cerebrus Action: Monitor call for keywords, prepare to transcribe
Sidebar displays: "Call in progress - I'll generate notes"
```

**09:15** - Engineer connects to customer's machine via Bomgar

```
Event: REMOTE_SESSION_CONNECTED
Context: remote_session_active=true
Cerebrus Action: Take screenshot, apply OCR
Sidebar updates: Shows customer's error messages
```

**09:20** - Call ends

```
Event: CALL_ENDED
Context: call_active=false
Cerebrus Action: Generate case summary from transcript + screenshots
Result: Pre-fill CRM with notes and next steps
```

## Adding New Event Types

1. Define in `app/services/event_detector.py`:

```python
@staticmethod
def detect_your_event(data) -> Optional[Tuple[str, Dict]]:
    # Your detection logic
    return ("YOUR_EVENT_TYPE", {"key": "value"})
```

2. Handle in `app/services/context_engine.py`:

```python
elif event_data.event_type == "YOUR_EVENT_TYPE":
    # Update context state
    context.your_field = event_data.data.get("your_key")
```

3. Test via API:

```bash
curl -X POST http://localhost:8000/api/events/ \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "YOUR_EVENT_TYPE",
    "source": "test",
    "data": {"key": "value"}
  }'
```

## Performance Considerations

- Events are processed synchronously (optimize for sub-100ms processing)
- Context state is queried frequently (cache or index on updated_at)
- Screenshots stored on disk, metadata in PostgreSQL
- Vector search is async in production (use task queue)
- Rate limit events per session to prevent spam
