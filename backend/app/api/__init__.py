"""API route initialization."""

from app.api.events import router as events_router
from app.api.context import router as context_router
from app.api.cases import router as cases_router
from app.api.knowledge import router as knowledge_router
from app.api.audio import router as audio_router

__all__ = ["events_router", "context_router", "cases_router", "knowledge_router", "audio_router"]
