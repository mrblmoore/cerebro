"""
Agents — named Cerebro workers, each with its own role, chats and jobs.

They all share one brain: the same memory, add-ons, skills and settings.
What differs is who they are (name, role, standing instructions), how they
look and sound (face, voice), and what they may use (tool scope).
"""

import json

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


def _load(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    #: One line: what this agent is for ("Researches cases").
    role = Column(String, nullable=True)
    instructions = Column(Text, nullable=True)
    #: brain | anime | live2d | vrm
    face = Column(String, default="brain")
    #: JSON: {"palette": "pink"} for anime; {"model": "<id>"} for live2d/vrm.
    face_style = Column(Text, nullable=True)
    voice_name = Column(String, nullable=True)
    voice_rate = Column(Float, nullable=True)
    #: JSON: how it sounds — provider, engine, pitch, volume, timbre, effects
    #: (see app.services.personality.clean_voice).
    voice_style = Column(Text, nullable=True)
    #: JSON: who it is — preset and sliders (see app.services.personality).
    personality = Column(Text, nullable=True)
    #: JSON list of tool groups it may use; empty means everything.
    tool_scope = Column(Text, nullable=True)
    color = Column(String, nullable=True)
    template = Column(String, nullable=True)
    is_default = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    sort = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())

    def style(self) -> dict:
        return _load(self.face_style, {})

    def scope(self) -> list:
        return _load(self.tool_scope, [])

    def voice(self) -> dict:
        return _load(self.voice_style, {})

    def persona(self) -> dict:
        return _load(self.personality, {})

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "role": self.role or "",
            "instructions": self.instructions or "", "face": self.face or "brain",
            "face_style": self.style(), "voice_name": self.voice_name or "",
            "voice_rate": self.voice_rate, "voice_style": self.voice(),
            "personality": self.persona(), "tool_scope": self.scope(),
            "color": self.color or "#8b7cf6", "template": self.template,
            "is_default": bool(self.is_default), "archived": bool(self.archived),
            "sort": self.sort or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
