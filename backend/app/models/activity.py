"""
Captured activity — the raw material the memory engine distils.

An ``ActivitySnapshot`` is one observation of what you were doing: the active
window, a downscaled screenshot, and any text captured around it. It is
short-lived by design — retention trims it — and everything durable is lifted
out of it into ``Memory`` before it expires.
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class ActivitySnapshot(Base):
    __tablename__ = "activity_snapshots"

    id = Column(Integer, primary_key=True, index=True)

    #: screenshot | keystrokes | window
    kind = Column(String, index=True)
    application = Column(String, nullable=True, index=True)
    window_title = Column(String, nullable=True)
    url = Column(String, nullable=True)

    #: Redacted text — typed words, or nothing for a bare screenshot. The raw
    #: text never reaches this column; redaction happens before it is stored.
    text = Column(Text, nullable=True)
    #: Rule names that fired during redaction, so we can see that a secret was
    #: removed without recording what it was.
    redacted = Column(String, nullable=True)

    #: Relative path to the screenshot under the activity directory, if any.
    screenshot_path = Column(String, nullable=True)

    case_id = Column(String, nullable=True, index=True)
    #: Set once the memory engine has consumed this snapshot.
    distilled = Column(DateTime, nullable=True, index=True)

    captured_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self, include_text: bool = False) -> dict:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "application": self.application,
            "window_title": self.window_title,
            "url": self.url,
            "screenshot_path": self.screenshot_path,
            "case_id": self.case_id,
            "redacted": [name for name in (self.redacted or "").split(",") if name],
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }
        if include_text:
            payload["text"] = self.text
        return payload

    def __repr__(self):
        return f"<ActivitySnapshot {self.kind} {self.application}>"
