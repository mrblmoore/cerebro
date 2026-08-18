"""
Tasks — the secretary half of Cerebro.

A task is something Cerebro should do or remember to do: a one-off ("remind me
to update case 500XY7 this afternoon"), a recurring job ("add a daily entry
under my name in the project log"), or a follow-up Cerebro raised itself ("we
resolved Randy's issue but never replied — send the update?").

Tasks carry a status, an optional schedule, and a JSON ``spec`` describing what
to do. Each run is recorded so nothing fires twice and the history is auditable.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, index=True)
    #: The user's own words, kept verbatim so the intent is never lost in parsing.
    instruction = Column(Text, nullable=True)

    #: reminder | document_update | draft_reply | summarise | custom
    kind = Column(String, index=True, default="reminder")
    #: JSON describing the concrete action (path, section, recipient, prompt…).
    spec = Column(Text, nullable=True)

    #: once | daily | weekdays | weekly | hourly | manual
    schedule = Column(String, default="once")
    #: HH:MM local time for day-based schedules.
    at_time = Column(String, nullable=True)

    #: pending | active | needs_review | done | failed | cancelled
    #: needs_review means Cerebro produced something waiting on the user.
    status = Column(String, default="active", index=True)

    #: Whether Cerebro may act on its own, or must present a draft for approval.
    autonomous = Column(Boolean, default=False)
    #: Persona/attribution for anything this task writes ("under my name").
    attribution = Column(String, nullable=True)

    case_id = Column(String, nullable=True, index=True)

    next_run = Column(DateTime, nullable=True, index=True)
    last_run = Column(DateTime, nullable=True)
    last_result = Column(Text, nullable=True)
    run_count = Column(Integer, default=0)

    source = Column(String, default="user")  # user | nudge | system
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        import json

        try:
            spec = json.loads(self.spec) if self.spec else {}
        except ValueError:
            spec = {}
        return {
            "id": self.id,
            "title": self.title,
            "instruction": self.instruction,
            "kind": self.kind,
            "spec": spec,
            "schedule": self.schedule,
            "at_time": self.at_time,
            "status": self.status,
            "autonomous": bool(self.autonomous),
            "attribution": self.attribution,
            "case_id": self.case_id,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_result": self.last_result,
            "run_count": self.run_count,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Task {self.title} ({self.status})>"
