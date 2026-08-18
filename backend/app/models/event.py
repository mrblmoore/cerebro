from sqlalchemy import Column, String, Integer, Text, DateTime, JSON
from sqlalchemy.sql import func
from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, index=True)  # CRM_CASE_OPENED, CALL_STARTED, etc.
    case_id = Column(String, nullable=True, index=True)
    
    # Event data
    source = Column(String)  # screenpipe, browser_extension, teams_integration
    data = Column(JSON)  # Flexible storage for event-specific data
    
    # Screenshots and context
    screenshot_path = Column(String, nullable=True)
    ocr_text = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=func.now(), index=True)
    processed = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<Event {self.event_type}>"
