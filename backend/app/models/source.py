"""Unified pieces of context Cerebro can actually read.

Events answer *what happened*.  Sources answer *what may be used to ground an
answer right now*.  Keeping that distinction explicit prevents a recent tab,
an unrelated document and an old screenshot from being blended together just
because they all happened to be in the database.
"""

import json

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, index=True)  # browser | document | sharepoint | activity | screenpipe
    stable_key = Column(String, unique=True, index=True)
    title = Column(String)
    uri = Column(String, nullable=True)
    local_path = Column(String, nullable=True)
    mime_type = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    content_hash = Column(String, nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)
    readable = Column(Boolean, default=False)
    active = Column(Boolean, default=True, index=True)
    excluded = Column(Boolean, default=False, index=True)
    error = Column(String, nullable=True)
    captured_at = Column(DateTime, default=func.now())
    last_seen = Column(DateTime, default=func.now(), onupdate=func.now(), index=True)

    def metadata_dict(self) -> dict:
        try:
            value = json.loads(self.metadata_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def to_dict(self, include_content: bool = False) -> dict:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "uri": self.uri,
            "local_path": self.local_path,
            "mime_type": self.mime_type,
            "readable": bool(self.readable),
            "active": bool(self.active),
            "excluded": bool(self.excluded),
            "error": self.error,
            "metadata": self.metadata_dict(),
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "characters": len(self.content or ""),
        }
        if include_content:
            payload["content"] = self.content
        return payload
