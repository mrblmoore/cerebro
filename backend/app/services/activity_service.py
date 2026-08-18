"""
Activity capture — storing what the desktop recorder sends, safely.

The desktop recorder is the eyes (screenshots) and hands (typed text); this is
where what it sees is filtered and kept. Every path in here runs captured
content through :mod:`redaction` before it touches the database, and a sensitive
window is dropped whole rather than redacted.

Nothing here records anything on its own — it stores what it is given, and only
when capture is switched on.
"""

import base64
import binascii
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.core.paths import ACTIVITY_DIR
from app.models.activity import ActivitySnapshot
from app.services import redaction


class ActivityService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------- guards
    @staticmethod
    def enabled() -> bool:
        return settings.ACTIVITY_CAPTURE_ENABLED

    @classmethod
    def _excluded(cls, window_title: str, application: str) -> bool:
        haystack = f"{window_title or ''} {application or ''}".lower()
        return any(app in haystack for app in settings.activity_excluded_apps)

    @classmethod
    def should_capture(cls, window_title: str, application: str) -> Dict[str, Any]:
        """Whether a frame may be captured, and why not when it may not."""
        if not cls.enabled():
            return {"capture": False, "reason": "capture disabled"}
        if cls._excluded(window_title, application):
            return {"capture": False, "reason": "excluded application"}
        if redaction.looks_sensitive(window_title, application):
            return {"capture": False, "reason": "sensitive window"}
        return {"capture": True}

    # ------------------------------------------------------------ storing
    def record(self, kind: str, application: str = None, window_title: str = None,
               url: str = None, text: str = None, screenshot_b64: str = None,
               case_id: str = None) -> Optional[ActivitySnapshot]:
        """
        Store one snapshot, after redaction and the sensitive-window check.

        Returns None when the frame was dropped — the caller does not need to
        care why, only that nothing sensitive was kept.
        """
        decision = self.should_capture(window_title, application)
        if not decision["capture"]:
            logger.debug("activity", "Frame dropped", {"reason": decision["reason"]})
            return None

        redacted_text = None
        fired: List[str] = []
        if text:
            redacted_text, fired = redaction.redact(
                text, redact_pii=settings.ACTIVITY_REDACT_PII)

        screenshot_path = None
        if screenshot_b64:
            screenshot_path = self._store_screenshot(screenshot_b64)

        snapshot = ActivitySnapshot(
            kind=kind,
            application=application,
            window_title=(window_title or "")[:400],
            url=url,
            text=redacted_text,
            redacted=",".join(fired) or None,
            screenshot_path=screenshot_path,
            case_id=case_id,
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)

        if fired:
            logger.info("activity", "Captured with redaction",
                        {"kind": kind, "removed": fired})
        return snapshot

    def _store_screenshot(self, screenshot_b64: str) -> Optional[str]:
        """Persist a base64 PNG under the activity directory, dated for retention."""
        try:
            raw = base64.b64decode(screenshot_b64, validate=True)
        except (binascii.Error, ValueError):
            logger.warn("activity", "Discarded a screenshot that was not valid base64")
            return None

        day = datetime.utcnow().strftime("%Y-%m-%d")
        folder = ACTIVITY_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.utcnow().strftime('%H%M%S%f')}.png"
        (folder / name).write_bytes(raw)
        return f"{day}/{name}"

    # ------------------------------------------------------------ reading
    def recent(self, limit: int = 50, kind: str = None,
               undistilled_only: bool = False) -> List[ActivitySnapshot]:
        query = self.db.query(ActivitySnapshot)
        if kind:
            query = query.filter(ActivitySnapshot.kind == kind)
        if undistilled_only:
            query = query.filter(ActivitySnapshot.distilled.is_(None))
        return query.order_by(ActivitySnapshot.captured_at.desc()).limit(limit).all()

    # --------------------------------------------------------- retention
    def purge(self, older_than_days: int = None, everything: bool = False) -> Dict[str, Any]:
        """Delete captured activity, honouring the retention window."""
        query = self.db.query(ActivitySnapshot)
        removed_files = 0

        if everything:
            snapshots = query.all()
        else:
            days = older_than_days if older_than_days is not None \
                else settings.ACTIVITY_RETENTION_DAYS
            if days <= 0:
                return {"ok": True, "deleted": 0, "detail": "retention disabled"}
            cutoff = datetime.utcnow() - timedelta(days=days)
            snapshots = query.filter(ActivitySnapshot.captured_at < cutoff).all()

        for snapshot in snapshots:
            if snapshot.screenshot_path:
                target = ACTIVITY_DIR / snapshot.screenshot_path
                try:
                    target.unlink(missing_ok=True)
                    removed_files += 1
                except OSError:
                    pass
            self.db.delete(snapshot)

        self.db.commit()
        self._cleanup_empty_dirs()
        logger.info("activity", "Purged activity",
                    {"snapshots": len(snapshots), "screenshots": removed_files})
        return {"ok": True, "deleted": len(snapshots), "screenshots": removed_files}

    def _cleanup_empty_dirs(self) -> None:
        try:
            for child in ACTIVITY_DIR.iterdir():
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        if not self.enabled():
            return {"ok": True, "enabled": False, "detail": "Activity capture disabled"}
        count = self.db.query(ActivitySnapshot).count()
        parts = []
        if settings.ACTIVITY_SCREENSHOTS:
            parts.append(f"screenshots every {settings.ACTIVITY_SCREENSHOT_SECONDS}s")
        if settings.ACTIVITY_KEYSTROKES:
            parts.append("typed text")
        retention = (f"{settings.ACTIVITY_RETENTION_DAYS}-day retention"
                     if settings.ACTIVITY_RETENTION_DAYS else "no automatic deletion")
        return {
            "ok": True, "enabled": True, "snapshots": count,
            "detail": f"Capturing {', '.join(parts) or 'nothing selected'} · {retention}",
        }


def apply_retention(db: Session) -> None:
    """Called periodically by the scheduler to trim old activity."""
    if settings.ACTIVITY_RETENTION_DAYS > 0:
        ActivityService(db).purge()
