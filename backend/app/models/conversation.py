"""
Conversations — separate chats in Ask, like Copilot or Grok.

Every :class:`~app.models.chat.ChatMessage` belongs to one conversation, so
"the Dynamics escalation" and "help me write a KB article" no longer share one
long thread and one memory. A conversation can carry standing instructions
("answer in French", "this chat is about case CAS-01234") that the agent
follows for every message in it, and tasks can be assigned to it: their
results post back into the same chat.

"End conversation" archives a chat (it stays readable and can be restored)
and opens a fresh one.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=True)
    #: Standing instructions the agent follows for every message in this chat.
    instructions = Column(Text, nullable=True)
    archived = Column(Boolean, default=False, index=True)
    pinned = Column(Boolean, default=False)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    last_message_at = Column(DateTime, nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title or "New chat",
            "instructions": self.instructions or "",
            "archived": bool(self.archived),
            "pinned": bool(self.pinned),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }

    def __repr__(self):
        return f"<Conversation {self.id} {self.title!r}>"
