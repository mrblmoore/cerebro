"""API route registration."""

from app.api.audio import router as audio_router
from app.api.cases import router as cases_router
from app.api.activity import router as activity_router
from app.api.context import router as context_router
from app.api.copilot import router as copilot_router
from app.api.documents import router as documents_router
from app.api.enterprise import router as enterprise_router
from app.api.events import router as events_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.style import router as style_router
from app.api.tasks import router as tasks_router
from app.api.system import router as system_router

ROUTERS = [
    system_router,
    events_router,
    context_router,
    cases_router,
    knowledge_router,
    enterprise_router,
    documents_router,
    activity_router,
    memory_router,
    style_router,
    tasks_router,
    copilot_router,
    audio_router,
]

__all__ = [
    "ROUTERS",
    "events_router",
    "context_router",
    "cases_router",
    "knowledge_router",
    "enterprise_router",
    "documents_router",
    "activity_router",
    "memory_router",
    "style_router",
    "tasks_router",
    "copilot_router",
    "audio_router",
    "system_router",
]
