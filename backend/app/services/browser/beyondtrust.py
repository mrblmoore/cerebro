"""
BeyondTrust Remote Support through the hidden browser (read-only).

The signed-in web console is opened with the user's own sign-in, so no API
account is needed. BeyondTrust consoles differ between appliances and
versions, so the connector tunes itself: when the start page lists no
sessions it follows the console's own "Sessions"/"Reports" links, remembers
the page that works, and tries other table layouts. What it learns is saved in
``DATA_DIR/connectors/beyondtrust.json`` — nobody has to edit that file, though
anything in it can still be overridden by hand.

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


_LINKS_JS = r"""
() => [...document.querySelectorAll('a[href]')].map(a => ({
  text: (a.innerText || a.getAttribute('aria-label') || a.title || '').replace(/\s+/g, ' ').trim(),
  href: a.href})).filter(l => l.text && l.text.length < 60 && !l.href.startsWith('javascript'))
"""

#: Link wording that leads to the page listing sessions, best first.
_NAV_WORDS = (("sessions", 6), ("session", 5), ("session history", 6), ("reports", 4),
              ("history", 4), ("conversations", 4), ("status", 3), ("activity", 3))
_NAV_AVOID = ("log out", "logout", "sign out", "download", "help", "password", "profile", "create")

#: Row layouts tried, in order, when the configured one finds nothing.
ROW_FALLBACKS = ("table tbody tr", "[role='row']", "[role='listitem']", "li",
                 "div[class*='row' i], div[class*='session' i]")


def nav_score(text: str) -> int:
    lowered = (text or "").lower()
    if any(word in lowered for word in _NAV_AVOID):
        return 0
    return max((score for word, score in _NAV_WORDS if word in lowered), default=0)


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

    def _rows_for(self, page, selector: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for frame in page.frames:
            try:
                rows.extend(frame.evaluate(_ROWS_JS, selector))
            except Exception:  # noqa: BLE001
                continue
        return rows

    def _rows(self, page) -> List[Dict[str, str]]:
        """Rows on the page. When the configured layout finds none, other
        common layouts are tried and the one that works is remembered."""
        configured = self.selectors().get("row") or DEFAULT_SELECTORS["row"]
        rows = self._rows_for(page, configured)
        if rows:
            return rows
        for candidate in ROW_FALLBACKS:
            rows = self._rows_for(page, candidate)
            if len(rows) >= 2:
                self.remember(row=candidate)
                return rows
        return []

    def _discover(self, page) -> bool:
        """Follow the console's own navigation to the page that lists
        sessions, and remember it as the place to start from next time."""
        best, best_score = None, 0
        for frame in page.frames:
            try:
                links = frame.evaluate(_LINKS_JS)
            except Exception:  # noqa: BLE001
                continue
            for link in links:
                score = nav_score(link["text"])
                if (score > best_score and self.on_own_site(link["href"])
                        and link["href"].split("#")[0] != page.url.split("#")[0]):
                    best, best_score = link, score
        if best is None:
            return False
        page.goto(best["href"], wait_until="domcontentloaded")
        self.settle(page)
        if not self.is_signed_in(page):
            return False
        self.remember(start_url=page.url, discovered_from=best["text"])
        return True

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
            hops = 0
            can_discover = not template and not self.selectors().get("start_url")
            while not rows and can_discover and hops < 2 and self._discover(page):
                hops += 1
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
