"""
Genesys Cloud through the hidden browser (read-only).

The signed-in web app (``apps.<region>``) is opened with the user's own
sign-in. Genesys Cloud's own app talks to its Platform API with a token it
keeps in the page's storage; the connector borrows that token *from inside the
signed-in page* to ask for conversations — no OAuth client needed — and falls
back to reading the page's text when no token can be found.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.services.browser import register
from app.services.browser.connector import _FETCH_JS, BrowserConnector, SignInRequired

DEFAULT_SELECTORS: Dict[str, Any] = {
    # Where conversations are looked up in the web app when the API isn't reachable.
    "interactions_url": "{base}/directory/#/engage/admin/interactions",
    "signed_in": "",
}
MAX_PAGES = 5

_TOKENS_JS = r"""
() => {
  const found = [];
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      const value = store.getItem(key) || '';
      if (!/token|auth/i.test(key)) continue;
      try {
        const data = JSON.parse(value);
        const token = typeof data === 'string' ? data
          : data.access_token || data.accessToken || data.token;
        if (token) found.push(String(token));
      } catch (e) {
        if (/^[A-Za-z0-9._~+\/-]{20,}=*$/.test(value)) found.push(value);
      }
    }
  }
  return found;
}
"""


class GenesysError(RuntimeError):
    pass


class GenesysConnector(BrowserConnector):
    name = "genesys"
    label = "Genesys Cloud"
    enabled_setting = "GENESYS_ENABLED"
    url_setting = "GENESYS_URL"
    default_selectors = DEFAULT_SELECTORS

    def __init__(self):
        super().__init__()
        self._token: Optional[str] = None

    def api_base(self) -> str:
        host = urlparse(self.base_url).netloc
        if host.startswith("apps."):
            host = "api." + host[len("apps."):]
        return f"https://{host}"

    def sign_in_url(self) -> str:
        return self.base_url

    # ------------------------------------------------------------- API
    def _call(self, page, method: str, path: str, body: Any = None) -> Dict[str, Any]:
        """A Platform API call made by the signed-in page, using its own token."""
        self.ensure_on_site(page)
        candidates = ([self._token] if self._token else []) + [
            t for t in (page.evaluate(_TOKENS_JS) or []) if t != self._token]
        if not candidates:
            raise GenesysError("Cerebro couldn't find Genesys Cloud's session token in the page.")
        for token in candidates:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            result = page.evaluate(_FETCH_JS, {"url": self.api_base() + path, "method": method,
                                               "body": body, "headers": headers})
            if result.get("status") == 401:
                continue
            self._token = token
            if result.get("status") == 403:
                raise GenesysError("Your Genesys Cloud account isn't allowed to view that "
                                   "(missing conversation or analytics permission).")
            if not result.get("ok"):
                raise GenesysError(f"Genesys Cloud returned HTTP {result.get('status')}.")
            return result.get("json") or {}
        self._token = None
        raise SignInRequired(self)

    def account_name(self, page) -> Optional[str]:
        try:
            return self._call(page, "GET", "/api/v2/users/me").get("name")
        except Exception:  # noqa: BLE001 - the name is a nicety
            return None

    # ----------------------------------------------------------- reads
    def search(self, query: str = "", days: int = 1, limit: int = 25) -> List[Dict[str, Any]]:
        """Conversations from the last ``days`` days, newest first; ``query``
        matches a phone number, name or address on any participant."""
        days = max(1, min(int(days or 1), 31))
        limit = max(1, min(int(limit or 25), 100))
        end = datetime.now(timezone.utc)
        interval = (f"{end - timedelta(days=days):%Y-%m-%dT%H:%M:%S.000Z}/"
                    f"{end:%Y-%m-%dT%H:%M:%S.000Z}")
        needle = (query or "").strip().lower()

        def work(page):
            self.ensure_on_site(page)
            found: List[Dict[str, Any]] = []
            for number in range(1, MAX_PAGES + 1):
                data = self._call(page, "POST", "/api/v2/analytics/conversations/details/query",
                                  {"interval": interval, "order": "desc",
                                   "orderBy": "conversationStart",
                                   "paging": {"pageSize": 100, "pageNumber": number}})
                batch = data.get("conversations") or []
                found.extend(c for c in batch if not needle or needle in str(c).lower())
                if len(batch) < 100 or len(found) >= limit:
                    break
            return [summary(c) for c in found[:limit]]

        return self.run(work, "Searching Genesys conversations")

    def conversation(self, conversation_id: str) -> Dict[str, Any]:
        def work(page):
            self.ensure_on_site(page)
            try:
                data = self._call(page, "GET",
                                  f"/api/v2/analytics/conversations/{conversation_id}/details")
                return {"source": "api", "raw": data, **summary(data)}
            except GenesysError:
                url = (self.selectors().get("interactions_url") or "").replace("{base}", self.base_url)
                page.goto(f"{url}/{conversation_id}", wait_until="domcontentloaded")
                self.settle(page)
                return {"source": "page", "id": conversation_id,
                        "text": self.readable_text(page)[:12000]}

        return self.run(work, "Reading Genesys conversation")


def summary(conversation: Dict[str, Any]) -> Dict[str, Any]:
    parts = conversation.get("participants") or []
    customer = next((p for p in parts if p.get("purpose") in ("customer", "external")), {})
    agent = next((p for p in parts if p.get("purpose") == "agent"), {})
    sessions = [s for p in parts for s in (p.get("sessions") or [])]
    first = sessions[0] if sessions else {}
    return {"id": conversation.get("conversationId"),
            "start": conversation.get("conversationStart"),
            "end": conversation.get("conversationEnd"),
            "media": first.get("mediaType"), "direction": first.get("direction"),
            "customer": customer.get("participantName") or first.get("ani") or "",
            "phone": first.get("ani") or "",
            "agent": agent.get("participantName") or "",
            "queues": sorted({s.get("queueId") for s in sessions if s.get("queueId")})}


connector = register(GenesysConnector())
