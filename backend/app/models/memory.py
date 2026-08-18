"""
Memory — what Cerebro has learned and carries forward.

This is the "second brain": durable facts distilled from your activity, events
and resolved cases, embedded so they can be recalled by relevance and injected
into prompts. Unlike ``ActivitySnapshot`` (raw and short-lived) a memory is
meant to last, and is written in a compact, self-contained sentence or two.
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)

    #: case_resolution | customer_fact | preference | procedure | style | fact
    memory_type = Column(String, index=True)
    #: One-line handle; the substance is in ``content``.
    title = Column(String)
    content = Column(Text)

    #: What this memory is about, for scoped recall.
    case_id = Column(String, nullable=True, index=True)
    customer = Column(String, nullable=True, index=True)
    #: Free-form comma-separated tags (applications, topics).
    tags = Column(String, nullable=True)

    #: Local embedding of ``title + content`` for semantic recall, and the
    #: signature of the space it belongs to (mirrors the document store).
    embedding = Column(Text, nullable=True)
    embedding_signature = Column(String, nullable=True)

    #: 0-1 confidence in the memory, and how often it has been recalled — both
    #: feed ranking so useful memories surface and stale guesses fade.
    confidence = Column(Float, default=0.6)
    use_count = Column(Integer, default=0)

    source = Column(String, nullable=True)  # activity | case | event | manual
    #: A user-pinned memory is never trimmed automatically.
    pinned = Column(Boolean, default=False)

    created_at = Column(DateTime, default=func.now(), index=True)
    last_used_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "title": self.title,
            "content": self.content,
            "case_id": self.case_id,
            "customer": self.customer,
            "tags": [tag for tag in (self.tags or "").split(",") if tag],
            "confidence": self.confidence,
            "use_count": self.use_count,
            "source": self.source,
            "pinned": bool(self.pinned),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }

    def __repr__(self):
        return f"<Memory {self.memory_type}: {self.title}>"
