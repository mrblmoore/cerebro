"""API routes for RAG and knowledge search."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core import get_db
from app.services.rag_service import RAGService
from typing import List

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/documents")
async def index_document(
    document: dict,
    db: Session = Depends(get_db)
):
    """Index a new document."""
    rag = RAGService(db)
    result = rag.index_document(document)
    return {
        "id": result.id,
        "vector_id": result.vector_id,
        "title": result.title
    }


@router.get("/search")
async def search_knowledge(
    query: str,
    limit: int = 5,
    db: Session = Depends(get_db)
):
    """Search for relevant documents."""
    if not query or len(query) < 3:
        raise HTTPException(status_code=400, detail="Query too short")
    
    rag = RAGService(db)
    results = rag.search(query, limit=limit)
    return {"query": query, "results": results}
