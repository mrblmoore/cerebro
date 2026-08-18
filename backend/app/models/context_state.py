from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON
from sqlalchemy.sql import func
from app.core.database import Base


class ContextState(Base):
    __tablename__ = "context_state"

    id = Column(Integer, primary_key=True, index=True)
    
    # Current context
    crm_case = Column(String, nullable=True)
    crm_system = Column(String, nullable=True)
    customer = Column(String, nullable=True)
    
    # Session state
    call_active = Column(Boolean, default=False)
    remote_session_active = Column(Boolean, default=False)
    remote_host = Column(String, nullable=True)
    
    # Application state
    active_application = Column(String, nullable=True)
    active_url = Column(String, nullable=True)
    window_title = Column(String, nullable=True)
    
    # AI state
    last_suggestion = Column(String, nullable=True)
    last_suggestion_time = Column(DateTime, nullable=True)
    
    # Metadata
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def to_dict(self):
        return {
            "crm_case": self.crm_case,
            "crm_system": self.crm_system,
            "customer": self.customer,
            "call_active": bool(self.call_active),
            "remote_session_active": bool(self.remote_session_active),
            "remote_host": self.remote_host,
            "active_application": self.active_application,
            "active_url": self.active_url,
            "window_title": self.window_title,
            "last_suggestion": self.last_suggestion,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    def __repr__(self):
        return f"<ContextState case={self.crm_case} call={self.call_active}>"
