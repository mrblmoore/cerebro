"""
Screenpipe client — optional desktop activity monitoring.

Screenpipe is off by default. Every method degrades to an empty result when it
is disabled or unreachable, so callers never need a try/except of their own.
Uses ``requests`` rather than ``aiohttp`` to keep the dependency list short.
"""

from typing import Any, Dict, List

import requests

from app.core import logger
from app.core.config import settings

TIMEOUT = 5


class ScreenpipeClient:
    def __init__(self, base_url: str = None):
        self.base_url = (base_url or settings.SCREENPIPE_URL).rstrip("/")

    @property
    def enabled(self) -> bool:
        return settings.SCREENPIPE_ENABLED

    def status(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": True, "enabled": False, "detail": "Screenpipe integration disabled"}
        try:
            response = requests.get(f"{self.base_url}/health", timeout=TIMEOUT)
            response.raise_for_status()
            return {"ok": True, "enabled": True, "detail": f"Connected at {self.base_url}"}
        except Exception as exc:
            return {"ok": False, "enabled": True,
                    "detail": f"Not reachable at {self.base_url} ({exc})"}

    def _get(self, path: str, params: Dict[str, Any] = None, default=None):
        if not self.enabled:
            return default
        try:
            response = requests.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warn("screenpipe_client", "Request failed", {"path": path, "error": str(exc)})
            return default

    def get_screenshots(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._get("/screenshots", {"limit": limit}, default=[]) or []

    def get_ocr(self, screenshot_id: str) -> str:
        data = self._get(f"/screenshots/{screenshot_id}/ocr", default={}) or {}
        return data.get("text", "")

    def get_active_window(self) -> Dict[str, Any]:
        return self._get("/windows/active", default={}) or {}

    def detect_applications(self) -> List[Dict[str, Any]]:
        return self._get("/applications", default=[]) or []
