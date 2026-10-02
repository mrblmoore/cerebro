"""
What Outlook and Teams in the hidden browser have in common.

Both are large single-page web apps whose screens change often, so neither
connector depends on how the page looks if it can help it:

1. **The app's own data.** While its tab is open, the web app keeps fetching
   JSON for itself — Outlook's ``service.svc`` calls, Teams' chat service.
   Cerebro listens to those responses (the page made them, with the page's own
   session; Cerebro never sees or stores a token) and reads messages out of
   them. The parsers walk the JSON looking for things *shaped* like a message,
   so a renamed wrapper or an extra level of nesting doesn't break them.
2. **The page itself**, as a fallback: selectors that can be corrected per
   tenant in ``connectors/<name>.json`` without a code change, exactly like
   RightAnswers.

When a step fails, the page's address and markup (hidden form values
removed, no cookies) are saved to ``connectors/<name>-capture.zip`` — the
thing to send if Outlook or Teams has changed and a selector needs updating.
"""

import hashlib
import html as html_lib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from app.core import logger
from app.core.config import settings
from app.core.paths import CONNECTORS_DIR
from app.services.browser.connector import BrowserConnector

#: Microsoft 365 apps hop between these hosts (old and new addresses); all of
#: them count as "on the app" rather than "sent somewhere else".
M365_HOSTS = ("cloud.microsoft", "office.com", "office365.com", "microsoft.com",
              "officeapps.live.com", "microsoftonline.com", "live.com")

#: How often a monitored tab is fully reloaded, as a safety net for updates
#: the app received over a push channel Cerebro can't see.
RELOAD_SECONDS = 15 * 60


class MessagingError(RuntimeError):
    pass


class MessagingConnector(BrowserConnector):
    keep_path = True
    #: Regular expression for response URLs worth reading.
    capture_pattern = r"$^"
    #: Hosts (suffixes) that count as this app.
    app_hosts: tuple = ()

    def __init__(self):
        super().__init__()
        self._pending: List[Any] = []        # responses not parsed yet
        self._hooked: set = set()
        self._opened_at = 0.0
        #: Who the signed-in user is: {"name", "email", "id"} as discovered.
        self.me: Dict[str, str] = {}
        #: Conversation id -> display name (Teams chat topics).
        self.names: Dict[str, str] = {}

    # ---------------------------------------------------------- the app
    def on_own_site(self, url: str) -> bool:
        host = (urlparse(url or "").hostname or "").lower()
        own = (urlparse(self.base_url).hostname or "").lower()
        if host and (host == own or any(host == h or host.endswith("." + h)
                                        for h in self.app_hosts)):
            return not self.looks_like_login(url)
        return False

    @property
    def auto_send(self) -> bool:
        return self.auto_apply

    def hook(self, page) -> None:
        """Listen to the app's own JSON responses in this tab (once per tab)."""
        if id(page) not in self._hooked:
            page.on("response", self._on_response)
            self._hooked.add(id(page))

    def goto(self, page, path: str = "") -> None:
        self.hook(page)
        super().goto(page, path)

    def _on_response(self, response) -> None:
        # Only remembered here: reading a body inside an event callback can
        # stall Playwright's sync API, so parsing happens in :meth:`drain`.
        try:
            if re.search(self.capture_pattern, response.url, re.IGNORECASE) \
                    and "json" in (response.headers.get("content-type") or ""):
                self._pending.append(response)
                del self._pending[:-200]
        except Exception:  # noqa: BLE001 - a response that went away
            pass

    def identity(self) -> Dict[str, str]:
        """Who "me" is: learned from the app, else the user's setting, else
        whoever the other Microsoft 365 connectors are signed in as."""
        if not self.me.get("name"):
            names = [n.strip() for n in (settings.INBOX_MY_NAMES or "").split(",") if n.strip()]
            for name in names:
                if "@" in name:
                    self.me.setdefault("email", name)
                else:
                    self.me.setdefault("name", name)
        if not self.me.get("name") or not self.me.get("email"):
            from app.services import browser

            for other in browser.connectors():
                account = getattr(other, "_account", None)
                if not account or other is self:
                    continue
                key = "email" if "@" in account else "name"
                self.me.setdefault(key, account)
        return self.me

    def drain(self) -> List[Dict[str, Any]]:
        """Messages found in the JSON the app fetched since the last call."""
        self.identity()
        pending, self._pending = self._pending, []
        found: List[Dict[str, Any]] = []
        for response in pending:
            try:
                data = response.json()
            except Exception:  # noqa: BLE001 - not JSON after all, or gone
                continue
            self.learn_identity(data)
            try:
                found.extend(self.parse(response.url, data))
            except Exception as exc:  # noqa: BLE001 - one odd response never stops the rest
                logger.warn("browser", "Couldn't read a response",
                            {"connector": self.name, "error": str(exc)[:200]})
        return found

    def ensure_open(self, page, reload_after: float = RELOAD_SECONDS) -> None:
        """Have the app open in its tab; reload it now and then."""
        self.hook(page)
        stale = time.time() - self._opened_at > reload_after
        if not self.on_own_site(page.url) or not self.on_main_view(page.url) or stale:
            self.goto(page)
            self._opened_at = time.time()
            page.wait_for_timeout(1500)      # first data arrives just after load

    @staticmethod
    def on_main_view(url: str) -> bool:
        """The app's main screen, not a compose window or a /l/ deep link it opened."""
        path = urlparse(url or "").path.lower()
        return "/deeplink/" not in path and not path.startswith("/l/")

    # ----------------------------------------------------- for subclasses
    def parse(self, url: str, data: Any) -> List[Dict[str, Any]]:
        return []

    def scrape(self, page) -> List[Dict[str, Any]]:
        """Messages read from the page itself (fallback)."""
        return []

    def learn_identity(self, data: Any) -> None:
        """Pick up who the signed-in user is from the app's own data."""

    # ------------------------------------------------------------ reading
    def collect(self, quiet: bool = True) -> List[Dict[str, Any]]:
        """Everything new the app has shown since the last call — the monitor's tick."""
        def work(page):
            self.ensure_open(page)
            page.wait_for_timeout(500)
            found = self.drain()
            if not found:
                found = self.scrape(page)
            return _unique(found)

        return self.run(work, f"Checking {self.label}", quiet=quiet)

    # ------------------------------------------------------------ helpers
    def locate(self, page, key: str, timeout: int = 0):
        """First visible match for selector ``key``, or ``None``."""
        selector = self.selectors().get(key) or ""
        if not selector:
            return None
        locator = page.locator(selector).first
        try:
            if timeout:
                locator.wait_for(state="visible", timeout=timeout)
            elif not locator.count():
                return None
        except Exception:  # noqa: BLE001
            return None
        return locator

    def require(self, page, key: str, what: str, timeout: int = 15000):
        found = self.locate(page, key, timeout=timeout)
        if found is None:
            self.capture(page, key)
            raise MessagingError(
                f"Cerebro couldn't find {what} in {self.label}. The page may have changed; "
                f"send %LOCALAPPDATA%\\Cerebro\\connectors\\{self.name}-capture.zip so the "
                f"“{key}” selector can be updated.")
        return found

    def capture(self, page, step: str) -> None:
        """Save the page as it was when ``step`` failed, for diagnosis."""
        try:
            CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
            bundle = CONNECTORS_DIR / f"{self.name}-capture.zip"
            markup = re.sub(r'(<input[^>]*type=["\']?hidden["\']?[^>]*value=)["\'][^"\']*["\']',
                            r'\1""', page.content(), flags=re.IGNORECASE)
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("step.txt", f"{step}\n{page.url}\n{datetime.now().isoformat()}")
                archive.writestr("page.html", markup)
        except Exception:  # noqa: BLE001 - diagnosis is best-effort
            pass

    @staticmethod
    def type_text(locator, text: str) -> None:
        """Type into a rich editor, keeping line breaks as new paragraphs."""
        locator.click()
        page = locator.page
        lines = (text or "").split("\n")
        for index, line in enumerate(lines):
            if line:
                page.keyboard.insert_text(line)
            if index < len(lines) - 1:
                page.keyboard.press("Shift+Enter")


# ------------------------------------------------------------- JSON tools
def walk(data: Any) -> Iterable[Dict[str, Any]]:
    """Every dict anywhere inside ``data``."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def iso(value: Any) -> Optional[str]:
    """A timestamp in ISO form, from the shapes these apps use."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _unique(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per message id, keeping the most complete version of it."""
    best: Dict[str, Dict[str, Any]] = {}
    for message in messages:
        key = message.get("id")
        if not key:
            continue
        current = best.get(key)
        if current is None or len(message.get("body") or "") > len(current.get("body") or ""):
            best[key] = {**(current or {}), **{k: v for k, v in message.items() if v not in (None, "")}}
    return list(best.values())


def mentions_me(text: str, me: Dict[str, str]) -> bool:
    lowered = (text or "").lower()
    names = [me.get("name") or "", (me.get("name") or "").split(" ")[0], me.get("email") or ""]
    return any(name and len(name) > 2 and f"@{name.lower()}" in lowered for name in names)


def settings_flag(name: str) -> bool:
    return bool(getattr(settings, name, False))
