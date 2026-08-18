"""
API for the optional Copilot Studio bridge.

Everything here is inert unless the integration is switched on — Cerebro works
exactly as before with it off.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.config import settings
from app.core.database import get_db
from app.services import copilot_bridge, copilot_guide
from app.services.copilot_bridge import ALLOWED_COMMANDS, CopilotBridge

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.get("/guide")
def guide() -> Dict[str, Any]:
    """Step-by-step setup, the agent instructions, tools and knowledge to add."""
    return copilot_guide.guide()


@router.get("/knowledge-file")
def knowledge_file() -> Response:
    """
    The file to upload as agent knowledge — how to read Cerebro's records.

    Served as a download so the user can hand it straight to Copilot Studio.
    """
    from app.services.copilot_guide import AGENT_INSTRUCTIONS

    body = f"""# Cerebro — how to read the shared files

Cerebro is a desktop assistant that watches what the engineer is doing locally.
It writes three files into the shared OneDrive folder. Read them before
answering questions about what the user is working on.

## context.json — what they are doing now
- `current_case`, `customer`, `crm_system` — the case in front of them
- `on_a_call`, `remote_session`, `remote_host` — live session state
- `active_application`, `window_title` — what is on screen
- `recent_documents` — files they have had open, with local paths
- `suggestions` — what Cerebro thinks they should do next
- `open_nudges` — things Cerebro has already flagged to them
- `generated_at` — how fresh this is. If it is hours old, say so.

## memory.json — what Cerebro has learned
A list of durable facts, each with `type`, `title`, `content`, and where it
applies (`case_id`, `customer`). Types include case_resolution, customer_fact,
preference, procedure. Prefer these over general knowledge, and say when you
are using one.

## style.json — how the user writes
- `style_card` / `guidance` — their voice in prose
- `profile` — measured traits (greeting, sign-off, sentence length, formality)
- `samples` — real examples of their writing
- `persona` — `assistant` (you/I) or `partner` (we/us)
Match this when drafting on their behalf.

## commands/ and results/
To have the desktop do something, write a JSON file into `commands/`. Cerebro
picks it up within about a minute and writes the outcome into `results/`.
Never claim the action is done in the same turn — say you have asked for it.

---

{AGENT_INSTRUCTIONS}
"""
    return Response(content=body, media_type="text/markdown", headers={
        "Content-Disposition": 'attachment; filename="cerebro-agent-knowledge.md"'})


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return CopilotBridge(db).status()


@router.get("/commands")
def commands() -> Dict[str, Any]:
    """What the agent is allowed to ask Cerebro to do."""
    return {
        "commands": [{"action": name, "description": text}
                     for name, text in sorted(ALLOWED_COMMANDS.items())],
        "mode": settings.COPILOT_COMMAND_MODE,
        "accepting": settings.COPILOT_ACCEPT_COMMANDS,
    }


@router.post("/sync", dependencies=[Depends(require_local_origin)])
def sync_now(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Publish now and pick up anything the agent has asked for."""
    if not settings.COPILOT_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=409,
            detail="The Copilot Studio integration is off. Turn it on in "
                   "Settings → Microsoft Copilot.")
    result = copilot_bridge.sync(db)
    if not result.get("ok"):
        detail = (result.get("published") or {}).get("detail") or "Sync failed."
        raise HTTPException(status_code=409, detail=detail)
    return result


@router.post("/test", dependencies=[Depends(require_local_origin)])
def test_bridge(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Check the folder end to end: write the files, then read one back.

    Backs the 'Test connection' button, so a misconfigured path fails here with
    a clear message rather than silently never reaching the agent.
    """
    bridge = CopilotBridge(db)
    folder = copilot_bridge.bridge_dir()

    if not settings.COPILOT_BRIDGE_ENABLED:
        return {"ok": False, "detail": "Integration is off — turn it on first."}
    if folder is None:
        return {"ok": False, "detail": "No folder set. Pick the OneDrive folder "
                                       "you created for Cerebro."}

    published = bridge.publish()
    if not published.get("ok"):
        return {"ok": False, "detail": published.get("detail")}

    context_file = folder / "context.json"
    if not context_file.exists():
        return {"ok": False, "detail": f"Wrote to {folder} but could not read it back."}

    return {
        "ok": True,
        "detail": (f"Working. Shared {len(published['written'])} file(s) in {folder}. "
                   "If this folder is inside OneDrive, your agent can read them "
                   "once it syncs."),
        "folder": str(folder),
        "written": published["written"],
    }
