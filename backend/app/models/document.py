from sqlalchemy import Column, String, Integer, Text, DateTime, Boolean
from sqlalchemy.sql import func
from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String)  # RightAnswers, SharePoint, etc.
    title = Column(String)
    content = Column(Text)
    url = Column(String, nullable=True)
    
    # Vector DB
    vector_id = Column(String, unique=True)  # Qdrant document ID
    
    # Metadata
    tags = Column(String, nullable=True)  # comma-separated
    indexed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<Document {self.title}>"
