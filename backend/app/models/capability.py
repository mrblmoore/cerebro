"""
Something Cerebro was given at runtime: an MCP server, a website, a skill.

The definition itself lives in a file under ``CAPABILITIES_DIR`` (so it can
be shared, backed up and read by hand); this row records its state — whether
it is on, whether it works, who added it and when it was last used.
"""

import json

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Capability(Base):
    __tablename__ = "capabilities"

    id = Column(Integer, primary_key=True, index=True)
    #: mcp | site | skill
    kind = Column(String, index=True)
    #: Unique within its kind; also the file name and the tool-name prefix.
    name = Column(String, index=True)
    label = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    #: ready | error | needs_sign_in | needs_approval | starting
    status = Column(String, default="starting")
    error = Column(Text, nullable=True)
    #: user | cerebro
    added_by = Column(String, default="user")
    #: Run actions that change things without an approval card (MCP only).
    auto_approve = Column(Boolean, default=False)
    tool_count = Column(Integer, nullable=True)
    #: JSON list of tool names, as last loaded.
    tools = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    last_used_at = Column(DateTime, nullable=True)

    def tool_list(self) -> list:
        try:
            return json.loads(self.tools or "[]")
        except ValueError:
            return []

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "name": self.name,
            "label": self.label or self.name, "description": self.description,
            "enabled": bool(self.enabled), "status": self.status, "error": self.error,
            "added_by": self.added_by, "auto_approve": bool(self.auto_approve),
            "tool_count": self.tool_count or 0, "tools": self.tool_list(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }
