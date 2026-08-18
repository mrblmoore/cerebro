"""
Nudges — Cerebro's proactive voice.

A nudge is something Cerebro decided is worth raising: an unanswered email, a
case resolved but never updated, a due reminder. It is phrased in the chosen
persona and shown in the widget and dashboard until the user acts on or dismisses
it. This is what makes Cerebro active rather than a passive dashboard.
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Nudge(Base):
    __tablename__ = "nudges"

    id = Column(Integer, primary_key=True, index=True)

    #: reminder | unanswered_email | case_not_updated | draft_reply | follow_up
    kind = Column(String, index=True)
    title = Column(String)
    #: The message shown to the user, already in persona voice.
    body = Column(Text)

    #: JSON describing a one-click action the nudge offers, if any.
    action = Column(Text, nullable=True)

    priority = Column(String, default="medium")  # high | medium | low
    status = Column(String, default="open", index=True)  # open | acted | dismissed

    #: Prevents the same situation raising a nudge twice.
    dedupe_key = Column(String, unique=True, index=True, nullable=True)

    task_id = Column(Integer, nullable=True)
    case_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=func.now(), index=True)
    acted_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        import json

        try:
            action = json.loads(self.action) if self.action else None
        except ValueError:
            action = None
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "action": action,
            "priority": self.priority,
            "status": self.status,
            "task_id": self.task_id,
            "case_id": self.case_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Nudge {self.kind}: {self.title}>"
