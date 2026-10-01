"""
A change Ask wants to make in an external system, waiting for approval.

Email and Teams drafts have their own table (:class:`EnterpriseAction`), shaped
around Power Automate. Everything else Ask can change — a Dynamics 365 note or
field, a RightAnswers article — is recorded here: which tool, with which
arguments, and a before/after preview the user approves or discards. Nothing
runs until it is approved.
"""

import json

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True, index=True)
    #: The executor that will run it, e.g. "dynamics_add_note".
    tool = Column(String, index=True)
    #: Which system it changes: "dynamics" | "rightanswers" | …
    integration = Column(String, index=True)
    title = Column(String)
    summary = Column(String, nullable=True)
    #: JSON: the arguments the executor runs with.
    args = Column(Text)
    #: JSON: what the user reviews — {"fields": [{"name", "before", "after"}]}
    #: and/or {"before": text, "after": text}.
    preview = Column(Text, nullable=True)

    #: draft → running → done | failed;  draft → discarded;  done → undone
    status = Column(String, default="draft", index=True)
    result = Column(Text, nullable=True)
    error = Column(String, nullable=True)

    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def args_dict(self) -> dict:
        try:
            value = json.loads(self.args or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def preview_dict(self) -> dict:
        try:
            value = json.loads(self.preview or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def result_dict(self) -> dict:
        try:
            value = json.loads(self.result or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tool": self.tool, "integration": self.integration,
            "title": self.title, "summary": self.summary,
            "args": self.args_dict(), "preview": self.preview_dict(),
            "status": self.status, "result": self.result, "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<AgentAction {self.tool} {self.status}>"
