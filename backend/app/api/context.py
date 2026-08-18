"""API routes for context state."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core import get_db
from app.schemas.context import ContextStateResponse
from app.services.context_engine import ContextEngine

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("/current", response_model=ContextStateResponse)
async def get_current_context(db: Session = Depends(get_db)):
    """Get the current context state."""
    engine = ContextEngine(db)
    context = engine.get_current_context() or engine.init_context()
    return context
