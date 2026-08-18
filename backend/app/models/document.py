from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String)  # RightAnswers, SharePoint, etc.
    title = Column(String)
    content = Column(Text)
    url = Column(String, nullable=True)

    # Vector store
    vector_id = Column(String, unique=True, index=True)
    #: JSON array of floats. Populated when the built-in vector store is active,
    #: which keeps knowledge search working without any external service.
    embedding = Column(Text, nullable=True)
    #: Embedding space the vector belongs to (provider:model:dim). Vectors from
    #: a different space are ignored rather than silently mis-ranked.
    embedding_signature = Column(String, nullable=True)

    # Metadata
    tags = Column(String, nullable=True)  # comma-separated
    indexed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Document {self.title}>"
