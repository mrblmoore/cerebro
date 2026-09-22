"""
Screenpipe client — optional desktop activity monitoring.

Screenpipe integration is ready by default. Every method degrades to an empty
result when it is disabled or unreachable, so callers never need a try/except
of their own. The current Screenpipe REST API exposes captured content through
``/search`` and service state through ``/health``.
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

    def search(self, limit: int = 10, content_type: str = "all", query: str = "") -> List[Dict[str, Any]]:
        data = self._get("/search", {
            "limit": limit,
            "content_type": content_type,
            "q": query or None,
        }, default={}) or {}
        if isinstance(data, list):
            return data
        return data.get("data") or data.get("items") or []

    def get_screenshots(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent OCR/screen records using Screenpipe's current API."""
        return self.search(limit=limit, content_type="ocr")

    def get_active_window(self) -> Dict[str, Any]:
        records = self.search(limit=1, content_type="ocr")
        return records[0] if records else {}

    def detect_applications(self) -> List[Dict[str, Any]]:
        records = self.search(limit=100, content_type="ocr")
        seen = set()
        applications = []
        for record in records:
            content = record.get("content", record) if isinstance(record, dict) else {}
            name = content.get("app_name") or content.get("application_name")
            if name and name not in seen:
                seen.add(name)
                applications.append({"name": name})
        return applications

    def sync_sources(self, db, limit: int = 8) -> Dict[str, Any]:
        """Bring recent Screenpipe OCR into Cerebro's shared source registry."""
        from app.services.source_service import SourceService

        records = self.get_screenshots(limit=limit)
        created = 0
        service = SourceService(db)
        for record in records:
            if not isinstance(record, dict):
                continue
            content = record.get("content") if isinstance(record.get("content"), dict) else record
            text = content.get("text") or content.get("ocr_text") or ""
            if len(str(text).strip()) < 20:
                continue
            title = (content.get("window_name") or content.get("window_title") or
                     content.get("app_name") or "Screen capture")
            timestamp = (content.get("timestamp") or record.get("timestamp") or
                         record.get("id") or f"{title}:{str(text)[:80]}")
            service.observe(
                "screenpipe", str(timestamp), str(title), uri=content.get("url"),
                content=str(text), readable=True, active=False,
                metadata={"application": content.get("app_name"),
                          "screenpipe_type": record.get("type")}, commit=False)
            created += 1
        if created:
            db.commit()
        return {"ok": True, "ingested": created}
