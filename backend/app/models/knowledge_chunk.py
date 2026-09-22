"""Section-sized retrieval units belonging to an indexed document."""

from sqlalchemy import Column, ForeignKey, Integer, String, Text

from app.core.database import Base


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True, nullable=False)
    position = Column(Integer, nullable=False)
    locator = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)
    embedding_signature = Column(String, nullable=True)

