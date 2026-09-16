"""
Chat — a real conversation, not just one-shot tasks.

Tasks and nudges cover "do this later" and "Cerebro raised this." Neither one
lets the user just talk to Cerebro: ask it something, give it more detail on
an instruction it did not fully understand, or have it ask a clarifying
question and get an answer back. A ``ChatMessage`` is one turn of that
conversation — user or assistant — kept so the widget can show a real thread
and so Cerebro can tell "this reply is the missing detail I asked for" from
"this is a new, unrelated message."
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)

    #: user | assistant
    role = Column(String, index=True)
    content = Column(Text)

    #: question | instruction | clarification | confirmation | answer
    kind = Column(String, nullable=True)

    #: JSON scratch space — for a clarification, the instruction so far and
    #: which field is missing, so the next reply can complete it instead of
    #: starting over. For an answer, this can also carry supporting images
    #: found in a document (``{"images": [{"image": name, "caption": ...}]}``).
    meta = Column(Text, nullable=True)

    #: Stored filename (see app.services.chat_images) of an image attached to
    #: this turn — a user's upload, or the file Cerebro asked to look at.
    image_path = Column(String, nullable=True)

    task_id = Column(Integer, nullable=True, index=True)
    case_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self) -> dict:
        import json

        try:
            meta = json.loads(self.meta) if self.meta else None
        except ValueError:
            meta = None
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "kind": self.kind,
            "meta": meta,
            "image_path": self.image_path,
            "task_id": self.task_id,
            "case_id": self.case_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<ChatMessage {self.role}: {(self.content or '')[:40]!r}>"
