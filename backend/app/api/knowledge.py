"""API routes for knowledge indexing and semantic search."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document
from app.services.rag_service import RAGService

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class DocumentCreate(BaseModel):
    title: str
    content: str
    source: Optional[str] = "manual"
    url: Optional[str] = None
    tags: Optional[List[str]] = None


@router.post("/documents")
def index_document(document: DocumentCreate, db: Session = Depends(get_db)):
    """Index a document so it becomes searchable."""
    try:
        result = RAGService(db).index_document(document.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "id": result.id,
        "vector_id": result.vector_id,
        "title": result.title,
        "source": result.source,
    }


@router.get("/documents")
def list_documents(limit: int = 50, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Browse indexed documents — powers the dashboard's Knowledge panel."""
    documents = (
        db.query(Document).order_by(Document.updated_at.desc()).limit(limit).all()
    )
    return {
        "count": len(documents),
        "documents": [
            {
                "id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "url": doc.url,
                "tags": [tag for tag in (doc.tags or "").split(",") if tag],
                "indexed": doc.indexed,
            }
            for doc in documents
        ],
    }


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    # Go through the service so the vector store is cleaned up too.
    RAGService(db).delete_document(document)
    return {"ok": True, "deleted": document_id}


@router.get("/search")
def search_knowledge(
    query: str = Query(..., min_length=2, description="What to search for"),
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Semantic search across indexed documents."""
    service = RAGService(db)
    results = service.search(query, limit=limit)
    return {
        "query": query,
        "backend": service.backend,
        "count": len(results),
        "results": results,
    }
