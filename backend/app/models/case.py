from sqlalchemy import Column, String, Integer, Text, DateTime, Boolean
from sqlalchemy.sql import func
from app.core.database import Base


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, unique=True, index=True)
    system = Column(String)  # Salesforce, ServiceNow, etc.
    customer = Column(String)
    title = Column(String)
    description = Column(Text, nullable=True)
    status = Column(String, default="open")
    
    # Context
    error_code = Column(String, nullable=True)
    application = Column(String, nullable=True)
    
    # Generated content
    ai_summary = Column(Text, nullable=True)
    troubleshooting_steps = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<Case {self.case_id}>"
