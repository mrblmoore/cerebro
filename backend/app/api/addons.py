"""
API for add-ons: MCP servers, websites and skills added at runtime.

``GET /api/addons`` lists them; the rest add, switch, sign in to, remove,
export and import them. Cerebro adds them itself too (through Ask's
``add_mcp_server`` / ``add_website`` / ``save_skill`` tools); this is the
same thing from the Connect tab.
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.services import capabilities, skills

router = APIRouter(prefix="/api/addons", tags=["addons"],
                   dependencies=[Depends(require_local_origin)])


class McpIn(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    #: A web address, or a command line ("uvx mcp-server-x --flag").
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list] = None
    transport: Optional[str] = None
    registry_name: Optional[str] = None
    #: Header (remote) or environment variable (local) name -> value.
    keys: Optional[Dict[str, str]] = None


class SiteIn(BaseModel):
    url: str
    name: Optional[str] = None
    description: Optional[str] = None


class AppIn(BaseModel):
    label: str
    path: Optional[str] = None
    window: Optional[str] = None
    description: Optional[str] = None


class SkillIn(BaseModel):
    name: str
    description: str = ""
    instructions: str


@router.get("")
def list_addons(db: Session = Depends(get_db)) -> Dict[str, Any]:
    items = capabilities.list_all(db)
    found = {s["slug"]: s for s in skills.all_skills()}
    for item in items:
        if item["kind"] == "skill" and item["name"] in found:
            item["instructions"] = found[item["name"]]["instructions"]
    return {"addons": items}


@router.get("/registry")
def search_registry(q: str) -> Dict[str, Any]:
    try:
        return {"servers": capabilities.search_registry(q, limit=8)}
    except Exception as exc:  # noqa: BLE001 - shown to the user
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/mcp")
def add_mcp(body: McpIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    import shlex

    keys = body.keys or {}
    if body.registry_name:
        options = [o for o in capabilities.search_registry(body.registry_name, limit=10)
                   if o["registry_name"] == body.registry_name]
        if not options:
            raise HTTPException(status_code=404, detail="That server isn't in the MCP Registry.")
        option = options[0]
        missing = [n["name"] for n in option["needs"] if not keys.get(n["name"])]
        if missing:
            raise HTTPException(status_code=400, detail=f"It needs: {', '.join(missing)}")
        config, label = option["config"], body.label or option["label"]
        description = option["description"]
    elif body.url and body.url.strip().startswith(("http://", "https://")):
        config = {"url": body.url.strip(), "source": "user"}
        if body.transport in ("http", "sse"):
            config["transport"] = body.transport
        label = body.label or body.name or body.url.split("//", 1)[-1].split("/", 1)[0]
        description = None
    else:
        line = (body.command or body.url or "").strip()
        parts = list(body.args or []) if body.command and body.args is not None else shlex.split(line)
        command = body.command or (parts.pop(0) if parts else "")
        if not command:
            raise HTTPException(status_code=400, detail="Give a web address or a command.")
        config = {"transport": "stdio", "command": command, "args": parts, "source": "user"}
        label = body.label or body.name or (parts[-1] if parts else command)
        description = None
    secret = {}
    if keys:
        secret = {"env": keys} if config.get("transport") == "stdio" else {"headers": keys}
    outcome = capabilities.add_mcp(db, body.name or label, config, added_by="user", label=label,
                                   description=description, secret=secret)
    if outcome.get("ok"):
        capabilities.record_added(db, "mcp", outcome["name"], outcome["label"],
                                  f"{outcome.get('count', 0)} tools", "user")
    return outcome


@router.post("/sites")
def add_site(body: SiteIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.core import settings_store
    from app.core.config import settings
    from app.services.browser import generic

    try:
        outcome = generic.add(db, body.name or "", body.url, body.description or "", added_by="user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not settings.BROWSER_AUTOMATION_ENABLED:
        settings_store.update({"BROWSER_AUTOMATION_ENABLED": True})
    capabilities.record_added(db, "site", outcome["name"], outcome["label"], outcome["url"], "user")
    return {"ok": True, **outcome}


@router.post("/apps")
def add_app(body: AppIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services import desktop_apps

    try:
        data = desktop_apps.register(db, body.label, path=body.path or "", window=body.window or "",
                                     added_by="user", description=body.description or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    capabilities.record_added(db, "app", data["name"], data["label"],
                              body.path or body.window or "", "user")
    return {"ok": True, "available": desktop_apps.available(), **data}


@router.post("/{kind}/{name}/auto-apply")
def set_auto_apply(kind: str, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Let a website's or desktop app's changes apply without asking."""
    enabled = bool(body.get("enabled"))
    if kind == "app":
        from app.services import desktop_apps

        try:
            desktop_apps.set_auto_apply(name, enabled)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    elif kind == "site":
        from app.services.browser import generic

        connector = generic._LOADED.get(name) or generic.load(name)
        if connector is None:
            raise HTTPException(status_code=404, detail="No such website")
        connector.set_auto_apply(enabled)
    else:
        raise HTTPException(status_code=400, detail="Only websites and desktop apps have this switch")
    return {"ok": True, "auto_apply": enabled}


@router.post("/skills")
def save_skill(body: SkillIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        return {"ok": True, **skills.save(db, body.name, body.description, body.instructions)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/skills/import")
async def import_skills(file: UploadFile, db: Session = Depends(get_db)) -> Dict[str, Any]:
    data = await file.read()
    try:
        names = skills.import_archive(db, data, file.filename or "")
    except Exception as exc:  # noqa: BLE001 - shown to the user
        raise HTTPException(status_code=400, detail=f"Couldn't import that: {exc}")
    return {"ok": True, "imported": names}


@router.post("/{kind}/{name}/enabled")
def set_enabled(kind: str, name: str, body: Dict[str, Any], db: Session = Depends(get_db)):
    return capabilities.set_enabled(db, kind, name, bool(body.get("enabled")))


@router.post("/mcp/{name}/auto-approve")
def set_auto_approve(name: str, body: Dict[str, Any], db: Session = Depends(get_db)):
    return capabilities.set_auto_approve(db, name, bool(body.get("enabled")))


@router.post("/mcp/{name}/refresh")
def refresh(name: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    capabilities._drop_connection(name)
    return capabilities.refresh_mcp(db, name)


@router.post("/mcp/{name}/sign-in")
def sign_in(name: str) -> Dict[str, Any]:
    return capabilities.begin_sign_in(name)


@router.get("/mcp/{name}/sign-in")
def sign_in_status(name: str) -> Dict[str, Any]:
    return capabilities.sign_in_state(name)


@router.post("/mcp/{name}/keys")
def set_keys(name: str, body: Dict[str, str], db: Session = Depends(get_db)) -> Dict[str, Any]:
    config = capabilities.mcp_config(name)
    if config is None:
        raise HTTPException(status_code=404, detail="No such add-on")
    where = "env" if (config.get("transport") == "stdio" or config.get("command")) else "headers"
    secret = capabilities.get_secret(name)
    secret[where] = {**(secret.get(where) or {}), **{k: v for k, v in body.items() if v}}
    capabilities.set_secret(name, secret)
    capabilities._drop_connection(name)
    return capabilities.refresh_mcp(db, name)


@router.delete("/{kind}/{name}")
def remove(kind: str, name: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return capabilities.remove(db, kind, name)


@router.get("/export")
def export_addons():
    return JSONResponse(capabilities.export_all(), headers={
        "Content-Disposition": 'attachment; filename="cerebro.cerebro-addons.json"'})


@router.post("/import")
async def import_addons(file: UploadFile, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        data = json.loads((await file.read()).decode("utf-8"))
        return capabilities.import_all(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/browser/take-control")
def take_control() -> Dict[str, Any]:
    from app.services.agent import web_tools

    return web_tools.take_control()


@router.post("/browser/release")
def release_control() -> Dict[str, Any]:
    from app.services.agent import web_tools

    return web_tools.release_control()
