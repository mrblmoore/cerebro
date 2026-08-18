"""The learned writing voice — one row, updated as samples accumulate."""

from sqlalchemy import Column, DateTime, Integer, Text
from sqlalchemy.sql import func

from app.core.database import Base


class StyleProfile(Base):
    __tablename__ = "style_profile"

    id = Column(Integer, primary_key=True)  # always STYLE_SINGLETON_ID

    #: JSON list of recent writing samples (redacted).
    samples = Column(Text, nullable=True)
    #: JSON of the computed feature profile.
    profile = Column(Text, nullable=True)
    #: Plain-language guidance derived from the profile.
    guidance = Column(Text, nullable=True)
    #: An LLM-written prose description of the voice, when available.
    style_card = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
