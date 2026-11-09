"""
What is this thing the user wants kept up to date?

``classify`` turns whatever the user gave — a link, a path, a program's name,
the name of an add-on — into a target Cerebro knows how to reach:

``sharepoint``  a SharePoint page or document (the built-in SharePoint tools)
``site``        a page on a website Cerebro is connected to (browse_* tools)
``web``         a page on a website it isn't connected to yet (add it first)
``file``        a local or synced Word/Excel/text file (read_document, edit_document)
``app``         a Windows desktop program (app_* tools)
``addon``       something reached through an MCP server that's been added
``unknown``     none of these — find a connector first

``connect`` then does whatever's needed so the agent can work on it: adds the
website (and asks the user to sign in once), registers the desktop program,
or says which connector to look for.
"""

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.core import logger

_URL = re.compile(r"https?://\S+", re.I)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def classify(db, target: str) -> Dict[str, Any]:
    from app.models.work import is_file_target

    text = (target or "").strip().strip('"').strip("<>")
    found = _URL.search(text)
    if found:
        url = found.group(0).rstrip(").,;")
        host = _host(url)
        if host.endswith(".sharepoint.com") or host.endswith(".sharepoint.us"):
            return {"kind": "sharepoint", "address": url, "label": _label_from_url(url),
                    "integration": "sharepoint", "connected": _sharepoint_on(),
                    "tools": "the SharePoint tools (sharepoint_*)"}
        connector = _site(url)
        if connector is not None:
            return {"kind": "site", "address": url, "label": connector.label,
                    "integration": connector.name, "connected": True,
                    "tools": "browse_open / browse_type / browse_click"}
        return {"kind": "web", "address": url, "label": host or url, "integration": None,
                "connected": False, "tools": "browse_open / browse_type / browse_click"}
    if is_file_target(text) or re.search(r"\.(docx|xlsx|pptx|csv|txt|md|pdf)$", text, re.I):
        from pathlib import Path

        from app.services.watches import file_path

        path = file_path(text) if is_file_target(text) else Path(text)
        synced = any(part.lower().startswith(("onedrive", "sharepoint")) for part in path.parts)
        return {"kind": "file", "address": str(path), "label": path.name,
                "integration": "documents", "connected": path.exists(), "synced": synced,
                "tools": "read_document / edit_document (Word, Excel) or write_file (text)"}
    from app.services import desktop_apps

    app = desktop_apps.match(text)
    if app:
        return {"kind": "app", "address": app, "label": (desktop_apps.definition(app) or {})
                .get("label") or app, "integration": f"app:{app}", "connected": True,
                "tools": "app_open / app_read / app_type / app_click / app_keys"}
    addon = _addon(db, text)
    if addon:
        return {"kind": "addon", "address": addon["name"], "label": addon.get("label") or addon["name"],
                "integration": addon["name"], "connected": addon.get("status") == "ready",
                "tools": f"the {addon.get('label') or addon['name']} tools"}
    return {"kind": "unknown", "address": text, "label": text[:60], "integration": None,
            "connected": False, "tools": ""}


def _label_from_url(url: str) -> str:
    path = [p for p in urlparse(url).path.split("/") if p]
    return (path[-1] if path else _host(url)).replace("%20", " ")[:80]


def _sharepoint_on() -> bool:
    from app.core.config import settings

    return bool(settings.BROWSER_AUTOMATION_ENABLED and settings.SHAREPOINT_BROWSER_ENABLED)


def _site(url: str):
    """The website or system ``url`` is on — even while the hidden browser is off,
    so a site that's already added isn't added twice."""
    try:
        from app.services import browser

        for connector in browser.connectors():
            try:
                if connector.on_own_site(url):
                    return connector
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001 - no hidden browser
        return None
    return None


def _addon(db, text: str) -> Optional[Dict[str, Any]]:
    from app.services import capabilities

    wanted = text.lower()
    if len(wanted) < 3:
        return None
    for row in capabilities.list_all(db):
        if row["kind"] != "mcp":
            continue
        names = [row["name"].lower(), (row.get("label") or "").lower()]
        if any(n and (n in wanted or wanted in n) for n in names):
            return row
    return None


def connect(db, target: Dict[str, Any], user_named: bool) -> Dict[str, Any]:
    """Do what's needed to reach the target. Returns ``{"ok", "cards", "note", "target"}``."""
    from app.core import settings_store
    from app.core.config import settings

    cards, kind = [], target["kind"]
    if kind == "web":
        if not user_named:
            return {"ok": False, "cards": [], "target": target,
                    "note": "That site isn't connected and the user didn't give its address; ask them."}
        from app.services import capabilities
        from app.services.browser import generic

        parsed = urlparse(target["address"])
        start = f"{parsed.scheme}://{parsed.netloc}/"
        outcome = generic.add(db, _host(target["address"]).split(".")[0].title(), start,
                              f"Added to keep {target['label']} updated", added_by="cerebro")
        if not settings.BROWSER_AUTOMATION_ENABLED:
            settings_store.update({"BROWSER_AUTOMATION_ENABLED": True})
        cards.append(capabilities.record_added(db, "site", outcome["name"], outcome["label"],
                                               outcome["url"], "cerebro"))
        cards.append({"type": "signin", "integration": outcome["name"],
                      "title": f"Sign in to {outcome['label']} once",
                      "detail": "So Cerebro can open and update the page as you."})
        target = {**target, "kind": "site", "integration": outcome["name"], "connected": True,
                  "label": target["label"]}
        logger.info("keep", "Connected a website to keep it updated", {"site": outcome["name"]})
        return {"ok": True, "cards": cards, "target": target,
                "note": f"Added the website {outcome['label']}; the user signs in once."}
    if kind == "sharepoint" and not target["connected"]:
        settings_store.update({"BROWSER_AUTOMATION_ENABLED": True, "SHAREPOINT_BROWSER_ENABLED": True})
        cards.append({"type": "signin", "integration": "sharepoint", "title": "Sign in to SharePoint once",
                      "detail": "So Cerebro can open and update it as you."})
        return {"ok": True, "cards": cards, "target": {**target, "connected": True},
                "note": "Turned on SharePoint; the user signs in once."}
    if kind == "site" and not settings.BROWSER_AUTOMATION_ENABLED:
        settings_store.update({"BROWSER_AUTOMATION_ENABLED": True})
        return {"ok": True, "cards": cards, "target": target,
                "note": "Turned the hidden browser on so Cerebro can reach the site."}
    if kind == "file" and not target["connected"]:
        return {"ok": False, "cards": [], "target": target,
                "note": f"There's no file at {target['address']}. Ask the user where it is."}
    if kind == "unknown":
        return {"ok": False, "cards": [], "target": target,
                "note": ("Cerebro can't reach that yet. Use find_connector to find a way in (an MCP "
                         "server, or add_website for an internal web app, or add_desktop_app for a "
                         "Windows program the user named), then call keep_updated again.")}
    return {"ok": True, "cards": cards, "target": target, "note": ""}
