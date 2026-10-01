"""
Routes for the hidden-browser integrations (RightAnswers, Dynamics 365).

``GET  /api/integrations``                      every integration and its state
``POST /api/integrations/{name}/auth/start``    open the sign-in window
``GET  /api/integrations/{name}/auth/status``   poll that sign-in
``POST /api/integrations/{name}/check``         is the saved session still valid?
``DELETE /api/integrations/{name}/auth``        forget the sign-in
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
    return {"browser": browser.engine().status(),
            "integrations": [connector.status() for connector in browser.connectors()]}


@router.get("/browser")
def browser_status() -> Dict[str, Any]:
    return browser.engine().status()


@router.post("/{name}/auth/start")
def start_sign_in(name: str) -> Dict[str, Any]:
    return _connector(name).begin_sign_in()


@router.get("/{name}/auth/status")
def sign_in_status(name: str) -> Dict[str, Any]:
    connector = _connector(name)
    return {**connector.sign_in_state(), "integration": connector.status()}


@router.post("/{name}/check")
def check(name: str) -> Dict[str, Any]:
    return _connector(name).check()


@router.delete("/{name}/auth")
def sign_out(name: str) -> Dict[str, Any]:
    return _connector(name).sign_out()
