"""
System routes — health, diagnostics and configuration.

These power the Setup wizard, the Settings screens and the widget's status
indicator. Secrets are never returned in clear text.
"""

import platform
import sys
from importlib import util as importlib_util
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import check_database, logger, settings, settings_store
from app.core.paths import ENV_FILE, PROJECT_ROOT
from app.core.database import get_db
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.screenpipe_client import ScreenpipeClient

router = APIRouter(prefix="/api/system", tags=["system"])

VERSION = "0.2.0"

OPTIONAL_PACKAGES = {
    "openai": ("AI generation via OpenAI", "backend/requirements-ai.txt"),
    "qdrant_client": ("Qdrant vector database", "backend/requirements-search.txt"),
}


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def _package_installed(name: str) -> bool:
    try:
        return importlib_util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def require_local_origin(request: Request) -> None:
    """
    Reject cross-site browser requests to the configuration endpoints.

    CORS defaults to ``*`` so the widget and browser extension work without
    fuss, but these routes read and write credentials — a database URL with its
    password, API keys, the LLM base URL. Any page the user happened to have
    open could otherwise read or rewrite them. Requests with no ``Origin``
    (curl, the CLI, the widget) and same-machine origins are allowed; anything
    else is refused.
    """
    origin = request.headers.get("origin")
    if not origin:
        return

    parsed = urlparse(origin)
    if parsed.scheme in ("chrome-extension", "moz-extension"):
        return
    if (parsed.hostname or "") in LOCAL_HOSTS:
        return
    if origin in settings.cors_origin_list:
        return

    raise HTTPException(
        status_code=403,
        detail="Configuration endpoints are restricted to local origins.",
    )



@router.get("/info")
async def info() -> Dict[str, Any]:
    """Identity and headline state — cheap enough to poll."""
    return {
        "name": settings.APP_NAME,
        "version": VERSION,
        "environment": settings.ENVIRONMENT,
        "setup_completed": settings.SETUP_COMPLETED,
        "ai_enabled": LLMService().enabled,
        "database": "sqlite" if settings.using_sqlite else "external",
        "docs_url": "/docs",
    }


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Every check the Setup screen shows, in one round trip."""
    llm = LLMService()
    rag = RAGService(db)

    checks = [
        {
            "id": "python",
            "label": "Python runtime",
            "ok": sys.version_info >= (3, 9),
            "detail": f"Python {platform.python_version()} on {platform.system()}",
            "required": True,
        },
        {
            "id": "database",
            "label": "Database",
            "required": True,
            **check_database(),
        },
        {
            "id": "config",
            "label": "Configuration file",
            "ok": ENV_FILE.exists(),
            "detail": (f"{ENV_FILE}" if ENV_FILE.exists()
                       else "Using built-in defaults — save settings to create backend/.env"),
            "required": False,
        },
        {
            "id": "knowledge",
            "label": "Knowledge search",
            "required": False,
            **rag.status(),
        },
        {
            "id": "ai",
            "label": "AI provider",
            "required": False,
            **llm.status(),
        },
        {
            "id": "screenpipe",
            "label": "Screenpipe capture",
            "required": False,
            **ScreenpipeClient().status(),
        },
    ]

    for name, (description, requirements_file) in OPTIONAL_PACKAGES.items():
        installed = _package_installed(name)
        checks.append({
            "id": f"pkg_{name}",
            "label": description,
            "ok": True,  # optional extras never fail the overall status
            "installed": installed,
            "optional_package": True,
            "required": False,
            "detail": ("Installed" if installed
                       else f"Not installed — pip install -r {requirements_file}"),
        })

    required_ok = all(check["ok"] for check in checks if check.get("required"))
    return {
        "ok": required_ok,
        "version": VERSION,
        "project_root": str(PROJECT_ROOT),
        "setup_completed": settings.SETUP_COMPLETED,
        "checks": checks,
    }


@router.get("/settings", dependencies=[Depends(require_local_origin)])
async def read_settings() -> Dict[str, Any]:
    """Editable configuration with secrets masked."""
    return settings_store.describe()


@router.put("/settings", dependencies=[Depends(require_local_origin)])
async def write_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Persist configuration changes to ``backend/.env``."""
    result = settings_store.update(changes)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["errors"])
    return result


@router.post("/setup/complete", dependencies=[Depends(require_local_origin)])
async def complete_setup() -> Dict[str, Any]:
    settings_store.mark_setup_complete()
    logger.info("system", "Setup wizard completed")
    return {"ok": True}


@router.post("/test/{target}", dependencies=[Depends(require_local_origin)])
def test_connection(target: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Verify one integration on demand, from the Settings screen."""
    if target == "database":
        return check_database()
    if target == "ai":
        return LLMService().test_connection()
    if target == "knowledge":
        return RAGService(db).status()
    if target == "screenpipe":
        return ScreenpipeClient().status()
    raise HTTPException(status_code=404, detail=f"Unknown test target: {target}")


@router.post("/reindex", dependencies=[Depends(require_local_origin)])
def reindex(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Re-embed all documents — run after changing the embedding provider."""
    return RAGService(db).reindex_all()


@router.get("/logs", dependencies=[Depends(require_local_origin)])
async def read_logs(lines: int = 200) -> Dict[str, Any]:
    """Tail of the log file, shown in the dashboard's Activity panel."""
    return {"path": settings.log_path, "lines": logger.tail(min(max(lines, 1), 1000))}
