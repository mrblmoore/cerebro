"""Optional direct SharePoint/OneDrive access through Microsoft Graph.

The existing local-sync resolver remains the simplest path and needs no cloud
credential. This connector is for remote-only libraries and exact, searchable
item access. It uses delegated read-only permissions and a device-code sign-in;
tokens are kept in Cerebro's local data directory.
"""

import base64
from importlib import util as importlib_util
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.core.config import settings
from app.core.paths import DATA_DIR

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Sites.Read.All", "Files.Read.All", "User.Read"]
TOKEN_CACHE = DATA_DIR / "microsoft-token-cache.json"
DOWNLOAD_DIR = DATA_DIR / "sharepoint_cache"
_LOGIN = {"status": "idle"}
_LOCK = threading.Lock()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", value or "document")[:180]


class SharePointService:
    def __init__(self):
        self.enabled = bool(settings.SHAREPOINT_GRAPH_ENABLED)

    def _cache(self):
        from msal import SerializableTokenCache

        cache = SerializableTokenCache()
        try:
            cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
        except OSError:
            pass
        return cache

    def _app(self, cache=None):
        if not settings.MICROSOFT_CLIENT_ID:
            raise RuntimeError("Enter the Microsoft application ID in Settings → Documents.")
        from msal import PublicClientApplication

        return PublicClientApplication(
            settings.MICROSOFT_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.MICROSOFT_TENANT_ID or 'common'}",
            token_cache=cache or self._cache(),
        )

    @staticmethod
    def _save_cache(cache) -> None:
        if cache.has_state_changed:
            TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_CACHE.write_text(cache.serialize(), encoding="utf-8")

    def access_token(self) -> Optional[str]:
        if not self.enabled or not settings.MICROSOFT_CLIENT_ID:
            return None
        try:
            cache = self._cache()
            app = self._app(cache)
            accounts = app.get_accounts()
            result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
            self._save_cache(cache)
            return (result or {}).get("access_token")
        except (ImportError, OSError):
            return None

    def begin_login(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "detail": "Direct SharePoint access is switched off."}
        try:
            cache = self._cache()
            app = self._app(cache)
            flow = app.initiate_device_flow(scopes=SCOPES)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
        if "user_code" not in flow:
            return {"ok": False, "detail": flow.get("error_description") or "Microsoft did not start sign-in."}

        with _LOCK:
            _LOGIN.clear()
            _LOGIN.update({
                "status": "waiting", "user_code": flow["user_code"],
                "verification_uri": flow.get("verification_uri") or
                                    flow.get("verification_uri_complete"),
                "message": flow.get("message"),
            })

        def complete():
            result = app.acquire_token_by_device_flow(flow)
            self._save_cache(cache)
            with _LOCK:
                if result.get("access_token"):
                    _LOGIN.update({"status": "connected", "ok": True,
                                   "detail": "SharePoint connected"})
                else:
                    _LOGIN.update({"status": "failed", "ok": False,
                                   "detail": result.get("error_description") or
                                             "Microsoft sign-in failed"})

        threading.Thread(target=complete, name="cerebro-microsoft-login", daemon=True).start()
        return {"ok": True, **dict(_LOGIN)}

    def login_state(self) -> Dict[str, Any]:
        if self.access_token():
            return {"ok": True, "status": "connected", "detail": "SharePoint connected"}
        with _LOCK:
            return {"ok": _LOGIN.get("status") == "waiting", **dict(_LOGIN)}

    def disconnect(self) -> Dict[str, Any]:
        try:
            TOKEN_CACHE.unlink(missing_ok=True)
        except OSError as exc:
            return {"ok": False, "detail": str(exc)}
        with _LOCK:
            _LOGIN.clear()
            _LOGIN.update({"status": "idle"})
        return {"ok": True, "detail": "SharePoint disconnected"}

    def _request(self, method: str, path: str, **kwargs):
        token = self.access_token()
        if not token:
            raise RuntimeError("Connect SharePoint in Sources before using it.")
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        response = requests.request(method, f"{GRAPH}{path}", headers=headers,
                                    timeout=30, **kwargs)
        response.raise_for_status()
        return response

    def status(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": True, "enabled": False,
                    "detail": "Direct SharePoint access disabled; local sync remains available"}
        if not settings.MICROSOFT_CLIENT_ID:
            return {"ok": False, "enabled": True,
                    "detail": "Microsoft application ID is missing"}
        if importlib_util.find_spec("msal") is None:
            return {"ok": False, "enabled": True,
                    "detail": "Microsoft sign-in component is not installed"}
        connected = bool(self.access_token())
        return {"ok": connected, "enabled": True, "connected": connected,
                "detail": "Connected with delegated read-only access" if connected
                          else "Ready to sign in"}

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        body = {"requests": [{
            "entityTypes": ["driveItem"],
            "query": {"queryString": query},
            "from": 0, "size": min(max(limit, 1), 50),
            "fields": ["id", "name", "webUrl", "size", "file", "parentReference"],
        }]}
        data = self._request("POST", "/search/query", json=body).json()
        containers = ((data.get("value") or [{}])[0].get("hitsContainers") or [])
        hits = containers[0].get("hits", []) if containers else []
        results = []
        for hit in hits:
            item = hit.get("resource") or {}
            parent = item.get("parentReference") or {}
            results.append({
                "id": item.get("id"), "drive_id": parent.get("driveId"),
                "name": item.get("name"), "web_url": item.get("webUrl"),
                "size": item.get("size"), "mime_type": (item.get("file") or {}).get("mimeType"),
                "summary": hit.get("summary"),
            })
        return results

    def open_item(self, drive_id: str, item_id: str, name: str = None,
                  web_url: str = None, db=None) -> Dict[str, Any]:
        metadata = self._request("GET", f"/drives/{drive_id}/items/{item_id}").json()
        filename = name or metadata.get("name") or item_id
        raw = self._request("GET", f"/drives/{drive_id}/items/{item_id}/content").content
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = DOWNLOAD_DIR / f"{item_id[:20]}-{_safe_name(filename)}"
        path.write_bytes(raw)
        return self._observe(path, web_url or metadata.get("webUrl"), db)

    def open_url(self, url: str, db=None) -> Dict[str, Any]:
        encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        item = self._request("GET", f"/shares/u!{encoded}/driveItem").json()
        parent = item.get("parentReference") or {}
        return self.open_item(parent.get("driveId"), item.get("id"),
                              item.get("name"), item.get("webUrl") or url, db)

    @staticmethod
    def _observe(path: Path, web_url: str, db=None) -> Dict[str, Any]:
        if db is None:
            return {"path": str(path), "web_url": web_url}
        from app.services.document_service import DocumentService

        record = DocumentService(db).observe(
            str(path), discovered_by="sharepoint_graph", web_url=web_url)
        return record.to_dict()
