from sqlalchemy import Column, String, Integer, Text, DateTime, JSON
from sqlalchemy.sql import func
from app.core.database import Base


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    
    # Memory type: case_resolution, customer_preference, etc.
    memory_type = Column(String, index=True)
    
    # Reference
    case_id = Column(String, nullable=True)
    customer = Column(String, nullable=True)
    
    # Content
    title = Column(String)
    content = Column(Text)
    
    # Vector DB
    vector_id = Column(String, unique=True)
    
    # Metadata
    relevance_score = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    accessed_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<Memory {self.memory_type}>"
