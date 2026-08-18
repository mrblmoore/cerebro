"""
API for activity capture — the eyes and hands of the second brain.

The desktop recorder posts frames here. Every stored frame is redacted first,
and a sensitive window is refused outright. Reading captured content back
requires a local origin, like the other privacy-sensitive endpoints.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.config import settings
from app.core.database import get_db
from app.services.activity_service import ActivityService

router = APIRouter(prefix="/api/activity", tags=["activity"])


class FrameIn(BaseModel):
    kind: str = "window"  # window | screenshot | keystrokes
    application: Optional[str] = None
    window_title: Optional[str] = None
    url: Optional[str] = None
    text: Optional[str] = None
    #: base64-encoded PNG, downscaled by the recorder before sending.
    screenshot_b64: Optional[str] = None
    case_id: Optional[str] = None


@router.get("/config")
def capture_config() -> Dict[str, Any]:
    """
    What the recorder should do — read on startup and refreshed periodically,
    so toggling capture in Settings reconfigures the recorder without a restart.
    """
    return {
        "enabled": settings.ACTIVITY_CAPTURE_ENABLED,
        "screenshots": settings.ACTIVITY_SCREENSHOTS,
        "screenshot_seconds": settings.ACTIVITY_SCREENSHOT_SECONDS,
        "screenshot_max_px": settings.ACTIVITY_SCREENSHOT_MAX_PX,
        "keystrokes": settings.ACTIVITY_KEYSTROKES,
        "excluded_apps": settings.activity_excluded_apps,
    }


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return ActivityService(db).status()


@router.post("/frame")
def submit_frame(frame: FrameIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Store one captured frame. Returns whether it was kept or dropped."""
    snapshot = ActivityService(db).record(
        kind=frame.kind, application=frame.application,
        window_title=frame.window_title, url=frame.url, text=frame.text,
        screenshot_b64=frame.screenshot_b64, case_id=frame.case_id,
    )
    if snapshot is None:
        return {"stored": False, "reason": "dropped (disabled, excluded or sensitive)"}
    return {"stored": True, "id": snapshot.id, "redacted": snapshot.to_dict()["redacted"]}


@router.get("", dependencies=[Depends(require_local_origin)])
def list_activity(limit: int = Query(50, ge=1, le=200), kind: Optional[str] = None,
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    snapshots = ActivityService(db).recent(limit=limit, kind=kind)
    return {"count": len(snapshots),
            "snapshots": [s.to_dict(include_text=True) for s in snapshots]}


@router.post("/purge", dependencies=[Depends(require_local_origin)])
def purge(older_than_days: Optional[int] = None, everything: bool = False,
          db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Delete captured activity — a retention run, or 'forget everything'."""
    return ActivityService(db).purge(older_than_days=older_than_days, everything=everything)
