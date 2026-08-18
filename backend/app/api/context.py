"""API routes for the live context state."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.context import ContextStateResponse
from app.services.context_engine import KNOWN_EVENTS, ContextEngine

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("/current", response_model=ContextStateResponse)
async def get_current_context(db: Session = Depends(get_db)):
    """The single source of truth for what the engineer is working on."""
    engine = ContextEngine(db)
    return engine.get_current_context() or engine.init_context()


@router.post("/reset", response_model=ContextStateResponse)
async def reset_context(db: Session = Depends(get_db)):
    """Clear the live context. Backs the widget's 'Reset context' action."""
    return ContextEngine(db).reset_context()


@router.get("/recommendations")
async def get_recommendations(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Suggestions for the current state — polled by the desktop widget."""
    engine = ContextEngine(db)
    return {"recommendations": engine.current_recommendations()}


@router.get("/event-types")
async def list_event_types() -> Dict[str, Any]:
    """Event vocabulary, used by the docs page and the extension options UI."""
    return {"event_types": [{"type": key, "description": value}
                            for key, value in KNOWN_EVENTS.items()]}
