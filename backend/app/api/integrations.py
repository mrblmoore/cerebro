"""
Routes for the hidden-browser integrations (RightAnswers, Dynamics 365).

``GET  /api/integrations``                      every integration and its state
``POST /api/integrations/{name}/enable``        switch it (and the browser) on
``POST /api/integrations/{name}/auth/start``    open the sign-in window
``GET  /api/integrations/{name}/auth/status``   poll that sign-in
``POST /api/integrations/{name}/check``         is the saved session still valid?
``DELETE /api/integrations/{name}/auth``        forget the sign-in
``POST /api/integrations/{name}/auto-apply``    make changes without approval (or not)
``POST /api/integrations/monitor``              watch Outlook and Teams (or not)
``POST /api/integrations/inbox/check``          check Outlook and Teams now
``GET  /api/integrations/browser``              the hidden browser itself
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.api.system import require_local_origin
from app.services import browser

router = APIRouter(prefix="/api/integrations", tags=["integrations"],
                   dependencies=[Depends(require_local_origin)])


def _connector(name: str):
    try:
        return browser.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")


@router.get("")
def list_integrations() -> Dict[str, Any]:
    from app.core.config import settings

    return {"browser": browser.engine().status(),
            "integrations": [connector.status() for connector in browser.connectors()],
            "monitor": {"enabled": bool(settings.INBOX_MONITOR_ENABLED),
                        "seconds": int(settings.INBOX_MONITOR_SECONDS or 60),
                        "assist": settings.INBOX_ASSIST, "notify": settings.INBOX_NOTIFY}}


@router.post("/monitor")
def set_monitor(body: Dict[str, Any]) -> Dict[str, Any]:
    """Watch Outlook and Teams for new messages (or stop)."""
    from app.core import settings_store

    result = settings_store.update({"INBOX_MONITOR_ENABLED": bool(body.get("enabled"))})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors"))
    return {"ok": True, "enabled": bool(body.get("enabled"))}


@router.post("/inbox/check")
def check_inbox() -> Dict[str, Any]:
    """Check Outlook and Teams now, instead of waiting for the next round."""
    from app.core.database import SessionLocal
    from app.services import inbox_monitor

    with SessionLocal() as db:
        return {"ok": True, "report": inbox_monitor.check_once(db)}


@router.get("/browser")
def browser_status() -> Dict[str, Any]:
    return browser.engine().status()


@router.post("/{name}/enable")
def enable(name: str) -> Dict[str, Any]:
    """Switch on the hidden browser and this system — the one-click "Sign in".

    The address is already pre-filled, so after this the sign-in window can
    open straight away. Nothing else changes.
    """
    from app.core import settings_store

    connector = _connector(name)
    result = settings_store.update({"BROWSER_AUTOMATION_ENABLED": True,
                                    connector.enabled_setting: True})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors"))
    return {"ok": True, "integration": connector.status()}


@router.post("/{name}/auto-apply")
def set_auto_apply(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Turn automatic changes on or off for a system that allows it."""
    from app.core import settings_store

    connector = _connector(name)
    if not connector.auto_apply_setting:
        raise HTTPException(status_code=400,
                            detail=f"Changes in {connector.label} always need your approval.")
    result = settings_store.update({connector.auto_apply_setting: bool(body.get("enabled"))})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors"))
    return {"ok": True, "integration": connector.status()}


@router.post("/{name}/auth/start")
def start_sign_in(name: str) -> Dict[str, Any]:
    return _connector(name).begin_sign_in()


@router.get("/{name}/auth/status")
def sign_in_status(name: str) -> Dict[str, Any]:
    connector = _connector(name)
    return {**connector.sign_in_state(), "integration": connector.status()}


@router.post("/{name}/teach/start")
def start_teaching(name: str) -> Dict[str, Any]:
    """Open the portal visibly and learn its layout from one search."""
    teacher = getattr(_connector(name), "teacher", None)
    if teacher is None:
        raise HTTPException(status_code=404, detail=f"{name} doesn't need teaching.")
    return teacher.start()


@router.get("/{name}/teach/status")
def teaching_status(name: str) -> Dict[str, Any]:
    teacher = getattr(_connector(name), "teacher", None)
    if teacher is None:
        raise HTTPException(status_code=404, detail=f"{name} doesn't need teaching.")
    return teacher.state()


@router.post("/{name}/check")
def check(name: str) -> Dict[str, Any]:
    return _connector(name).check()


@router.delete("/{name}/auth")
def sign_out(name: str) -> Dict[str, Any]:
    return _connector(name).sign_out()
