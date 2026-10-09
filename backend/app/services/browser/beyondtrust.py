"""
BeyondTrust Remote Support through the hidden browser (read-only).

The signed-in web console is opened with the user's own sign-in, so no API
account is needed. BeyondTrust consoles differ between appliances and
versions, so the connector is selector-driven like RightAnswers: the defaults
read whatever tables a page shows, and anything that needs adjusting goes in
``DATA_DIR/connectors/beyondtrust.json`` (only the keys that differ).

Two ways in: ``read_page`` opens any console page or report link the user
pastes, and ``search`` looks something up from the configured start page
(optionally through a ``search_url`` template such as
``{base}/login/reports?query={query}``).
"""

import re
from typing import Any, Dict, List
from urllib.parse import quote_plus, urlparse

from app.services.browser import register
from app.services.browser.connector import (BrowserConnector, LOGIN_MARKERS,
                                            NotConfigured, SignInRequired)

DEFAULT_SELECTORS: Dict[str, Any] = {
    "search_url": "",
    "search_input": ("input[type='search'], input[name*='search' i], input[id*='search' i], "
                     "input[placeholder*='search' i], input[placeholder*='session' i]"),
    "row": "table tbody tr, [role='row'], li.session, .session-row",
    "signed_in": "",
}
MAX_ROWS = 60
SESSION_ID_RE = re.compile(r"\b[0-9a-f]{32}\b|\b\d{6,}\b", re.IGNORECASE)

#: "/login" is where BeyondTrust's console itself lives, so it can't mean "sign-in page".
_IDP_MARKERS = tuple(m for m in LOGIN_MARKERS if m != "/login")

_ROWS_JS = r"""
(selector) => {
  const out = [];
  for (const row of document.querySelectorAll(selector)) {
    const text = (row.innerText || '').replace(/\s+/g, ' ').trim();
    if (text.length < 8) continue;
    const link = row.querySelector('a[href]');
    out.push({text: text.slice(0, 600), href: link ? link.href : ''});
  }
  return out;
}
"""


class BeyondTrustError(RuntimeError):
    pass


class BeyondTrustConnector(BrowserConnector):
    name = "beyondtrust"
    label = "BeyondTrust"
    keep_path = True
    enabled_setting = "BEYONDTRUST_ENABLED"
    url_setting = "BEYONDTRUST_URL"
    default_selectors = DEFAULT_SELECTORS

    @staticmethod
    def looks_like_login(url: str) -> bool:
        lowered = (url or "").lower()
        return any(marker in lowered for marker in _IDP_MARKERS)

    def sign_in_url(self) -> str:
        return self.start_url or self.url("/login")

    def is_signed_in(self, page) -> bool:
        if not self.on_own_site(page.url) or self.looks_like_login(page.url):
            return False
        for frame in page.frames:
            try:
                if frame.locator("input[type='password']").first.is_visible(timeout=800):
                    return False
            except Exception:  # noqa: BLE001
                continue
        marker = self.selectors().get("signed_in")
        if marker:
            try:
                return page.locator(marker).first.is_visible(timeout=1500)
            except Exception:  # noqa: BLE001
                return False
        return True

    def _open(self, page, url: str = "") -> None:
        page.goto(url or self.sign_in_url(), wait_until="domcontentloaded")
        self.settle(page)
        if not self.is_signed_in(page):
            raise SignInRequired(self)

    def _text(self, page) -> str:
        parts = []
        for frame in page.frames:
            try:
                text = frame.evaluate("() => (document.body && document.body.innerText) || ''")
            except Exception:  # noqa: BLE001
                continue
            if text and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    def _rows(self, page) -> List[Dict[str, str]]:
        selector = self.selectors().get("row") or DEFAULT_SELECTORS["row"]
        rows: List[Dict[str, str]] = []
        for frame in page.frames:
            try:
                rows.extend(frame.evaluate(_ROWS_JS, selector))
            except Exception:  # noqa: BLE001
                continue
        return rows

    # ------------------------------------------------------------ reads
    def read_page(self, target: str) -> Dict[str, Any]:
        """Open a console page or report link on this site and read it."""
        url = self.url(target) if target else self.start_url
        if not self.on_own_site(url):
            raise BeyondTrustError(f"{target} isn't on {self.base_url}; only your BeyondTrust "
                                   "site can be opened.")

        def work(page):
            self._open(page, url)
            return {"url": page.url, "title": page.title(), "text": self._text(page)[:20000]}

        return self.run(work, "Reading BeyondTrust page")

    def search(self, query: str = "", limit: int = 25) -> Dict[str, Any]:
        """Rows matching ``query`` on the start page (or the search_url page).

        Returns ``{"url", "sessions": [{id, text, url}], "text"}``; ``text`` is
        the whole page's text, kept so something is still returned when the
        page has no table the row selector recognises.
        """
        limit = max(1, min(int(limit or 25), MAX_ROWS))
        needle = (query or "").strip()

        def work(page):
            template = self.selectors().get("search_url")
            if template:
                target = template.replace("{base}", self.base_url).replace("{query}", quote_plus(needle))
                self._open(page, target)
            else:
                self._open(page)
                if needle:
                    self._fill_search(page, needle)
            rows = self._rows(page)
            if needle and not template:
                lowered = needle.lower()
                filtered = [r for r in rows if lowered in r["text"].lower()]
                rows = filtered or rows
            sessions = []
            for r in rows[:limit]:
                found_id = SESSION_ID_RE.search(r["text"])
                sessions.append({"id": found_id.group(0) if found_id else "",
                                 "text": r["text"], "url": r["href"]})
            return {"url": page.url, "sessions": sessions, "text": self._text(page)[:12000]}

        return self.run(work, "Searching BeyondTrust")

    def _fill_search(self, page, needle: str) -> None:
        selector = self.selectors().get("search_input") or DEFAULT_SELECTORS["search_input"]
        for frame in page.frames:
            try:
                box = frame.locator(selector).first
                if box.is_visible(timeout=1500):
                    box.fill(needle)
                    box.press("Enter")
                    self.settle(page)
                    return
            except Exception:  # noqa: BLE001
                continue


connector = register(BeyondTrustConnector())
