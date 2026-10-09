"""Genesys Cloud, through its Platform API (read-only)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.core.config import settings
from app.services.systems.base import ApiConnector, SystemCallError


class Genesys(ApiConnector):
    name = "genesys"
    label = "Genesys Cloud"
    enabled_setting = "GENESYS_ENABLED"
    required = (("GENESYS_REGION", "region"),
                ("GENESYS_CLIENT_ID", "OAuth client ID"),
                ("GENESYS_CLIENT_SECRET", "OAuth client secret"))

    def _region(self) -> str:
        """``mypurecloud.com``, ``usw2.pure.cloud``, ... (a pasted URL is fine)."""
        region = (settings.GENESYS_REGION or "").strip().lower()
        for prefix in ("https://", "http://", "login.", "api.", "apps."):
            if region.startswith(prefix):
                region = region[len(prefix):]
        return region.split("/")[0]

    def api_base(self) -> str:
        return f"https://api.{self._region()}"

    def token_url(self) -> str:
        return f"https://login.{self._region()}/oauth/token"

    def credentials(self):
        return settings.GENESYS_CLIENT_ID, settings.GENESYS_CLIENT_SECRET

    def display_address(self) -> str:
        return self._region()

    def verify(self) -> str:
        self.bearer(force=True)
        info = self.request("GET", "/api/v2/organizations/me").json()
        return f"Connected to Genesys Cloud organisation {info.get('name') or ''}".strip() + "."

    # ----------------------------------------------------------- reads
    def search_conversations(self, days: int = 1, query: str = "", limit: int = 25) -> List[Dict[str, Any]]:
        """Conversations from the last ``days`` days, newest first. ``query``
        matches a phone number, name or address on any participant."""
        days = max(1, min(int(days or 1), 31))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        interval = f"{start:%Y-%m-%dT%H:%M:%S.000Z}/{end:%Y-%m-%dT%H:%M:%S.000Z}"
        needle = (query or "").strip().lower()
        found: List[Dict[str, Any]] = []
        for page in range(1, 6):
            body = {"interval": interval, "order": "desc", "orderBy": "conversationStart",
                    "paging": {"pageSize": 100, "pageNumber": page}}
            response = self.request("POST", "/api/v2/analytics/conversations/details/query", json=body)
            batch = (response.json() or {}).get("conversations") or []
            for conversation in batch:
                if not needle or needle in str(conversation).lower():
                    found.append(conversation)
            if len(batch) < 100 or len(found) >= limit:
                break
        return found[:max(1, int(limit))]

    def conversation(self, conversation_id: str) -> Dict[str, Any]:
        try:
            return self.request(
                "GET", f"/api/v2/analytics/conversations/{conversation_id}/details").json()
        except SystemCallError as exc:
            if "404" in str(exc):
                raise SystemCallError(f"Genesys has no conversation {conversation_id}.") from exc
            raise


def summary(conversation: Dict[str, Any]) -> Dict[str, Any]:
    parts = conversation.get("participants") or []
    customer = next((p for p in parts if p.get("purpose") in ("customer", "external")), {})
    agent = next((p for p in parts if p.get("purpose") == "agent"), {})
    queues = sorted({s.get("queueId") for p in parts for s in (p.get("sessions") or [])
                     if s.get("queueId")})
    sessions = [s for p in parts for s in (p.get("sessions") or [])]
    first = sessions[0] if sessions else {}
    return {"id": conversation.get("conversationId"),
            "start": conversation.get("conversationStart"),
            "end": conversation.get("conversationEnd"),
            "media": first.get("mediaType"), "direction": first.get("direction"),
            "customer": customer.get("participantName") or first.get("ani") or "",
            "phone": first.get("ani") or "",
            "agent": agent.get("participantName") or "",
            "queues": queues,
            "external_tag": conversation.get("externalTag") or ""}


connector = Genesys()
