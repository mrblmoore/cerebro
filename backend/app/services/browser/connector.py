"""
Connector base: one external web system Cerebro works in through the hidden
browser (RightAnswers, Dynamics 365).

A connector knows three things about its system:

* **where it is** — a URL from settings;
* **whether the browser is signed in** — :meth:`BrowserConnector.is_signed_in`;
* **its page layout** — selectors, loaded from defaults in code and then
  overridden by ``DATA_DIR/connectors/<name>.json`` so a tenant whose layout
  differs can be adjusted without a new release.

Sign-in uses the user's own account. "Sign in" reopens Cerebro's browser
profile in a visible window at the system's address; the user signs in as
they normally would (single sign-on usually completes by itself), and once
the connector sees a signed-in page the window closes and the browser goes
back to working hidden, reusing that session. No password is ever seen or
stored by Cerebro.
"""

import json
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urljoin, urlparse

from app.core import logger
from app.core.config import settings
from app.core.paths import CONNECTORS_DIR
from app.services.browser.engine import BrowserUnavailable, engine

#: Hosts and path fragments that mean "this is a sign-in page".
LOGIN_MARKERS = (
    "login.microsoftonline.com", "login.microsoft.com", "login.live.com",
    "login.windows.net", "/adfs/", "okta.com", "auth0.com", "/saml", "/sso",
    "/login", "/signin", "/sign-in", "/logon",
)

SIGN_IN_TIMEOUT_SECONDS = 600
SIGN_IN_POLL_SECONDS = 2.0

_FETCH_JS = """
async ({url, method, body, headers}) => {
  const init = {method, credentials: 'include', headers: headers || {}};
  if (body !== null && body !== undefined) {
    init.body = typeof body === 'string' ? body : JSON.stringify(body);
  }
  const response = await fetch(url, init);
  const text = await response.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch (e) { json = null; }
  return {
    status: response.status, ok: response.ok, url: response.url,
    json, text: json === null ? text.slice(0, 20000) : null,
    entity_id: response.headers.get('OData-EntityId'),
    etag: response.headers.get('ETag'),
  };
}
"""

_READABLE_ELEMENT_JS = """
(root) => {
  if (!root) return '';
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, nav, header, footer, svg, [aria-hidden="true"]')
    .forEach(node => node.remove());
  return (clone.innerText || clone.textContent || '').replace(/\\n{3,}/g, '\\n\\n').trim();
}
"""

_READABLE_JS = """
(selector) => {
  const root = (selector && document.querySelector(selector))
    || document.querySelector('main, article, [role="main"]') || document.body;
  if (!root) return '';
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, nav, header, footer, svg, [aria-hidden="true"]')
    .forEach(node => node.remove());
  return (clone.innerText || clone.textContent || '').replace(/\\n{3,}/g, '\\n\\n').trim();
}
"""


def normalise_address(value) -> str:
    """``dental.crm.dynamics.com/main.aspx?…`` → ``https://dental.crm.dynamics.com``."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")


class SignInRequired(RuntimeError):
    """The browser is not (or no longer) signed in to this system."""

    def __init__(self, connector: "BrowserConnector"):
        super().__init__(f"Cerebro is not signed in to {connector.label}. "
                         f"Click “Sign in” for {connector.label} and sign in once.")
        self.connector = connector


class NotConfigured(RuntimeError):
    """The connector is switched off or has no address."""


class BrowserConnector:
    #: Short identifier, used in URLs and file names.
    name = ""
    #: Shown to the user.
    label = ""
    #: ``settings`` attributes that switch it on and hold its address.
    enabled_setting = ""
    url_setting = ""
    #: ``settings`` attribute that lets its changes skip approval ("" = never).
    auto_apply_setting = ""
    #: Default selectors; tenants override them in CONNECTORS_DIR/<name>.json.
    default_selectors: Dict[str, Any] = {}

    def __init__(self):
        self._state_lock = threading.Lock()
        self._sign_in: Dict[str, Any] = {"status": "idle"}
        self._signed_in: Optional[bool] = None
        self._checked_at: Optional[float] = None
        self._account: Optional[str] = None

    # ----------------------------------------------------------- config
    @property
    def base_url(self) -> str:
        """The system's address, normalised to ``https://host``.

        People type ``dental.crm.dynamics.com`` or paste a whole case link;
        both systems live at the root of their host, so only the host is kept.
        """
        return normalise_address(getattr(settings, self.url_setting, None))

    @property
    def origin(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

    @property
    def enabled(self) -> bool:
        return bool(settings.BROWSER_AUTOMATION_ENABLED
                    and getattr(settings, self.enabled_setting, False)
                    and self.base_url)

    def require_enabled(self) -> None:
        if not settings.BROWSER_AUTOMATION_ENABLED:
            raise NotConfigured("The hidden browser is switched off (Settings → "
                                "RightAnswers, Dynamics & SharePoint).")
        if not getattr(settings, self.enabled_setting, False):
            raise NotConfigured(f"{self.label} is switched off (Settings → "
                                "RightAnswers, Dynamics & SharePoint).")
        if not self.base_url:
            raise NotConfigured(f"Enter your {self.label} address in Settings → "
                                "RightAnswers, Dynamics & SharePoint.")

    def selectors(self) -> Dict[str, Any]:
        merged = dict(self.default_selectors)
        override = CONNECTORS_DIR / f"{self.name}.json"
        try:
            data = json.loads(override.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            logger.warn("browser", "Ignoring unreadable connector override",
                        {"connector": self.name, "error": str(exc)})
        return merged

    def url(self, path: str = "") -> str:
        if not path:
            return self.base_url
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def sign_in_url(self) -> str:
        return self.base_url

    # --------------------------------------------------------- sign-in
    @staticmethod
    def looks_like_login(url: str) -> bool:
        lowered = (url or "").lower()
        return any(marker in lowered for marker in LOGIN_MARKERS)

    def on_own_site(self, url: str) -> bool:
        return bool(self.origin) and (url or "").lower().startswith(self.origin.lower())

    def is_signed_in(self, page) -> bool:
        """Default check: on the system's own site, not on a sign-in page,
        and (when the connector defines one) the signed-in marker is present."""
        if not self.on_own_site(page.url) or self.looks_like_login(page.url):
            return False
        marker = self.selectors().get("signed_in")
        if not marker:
            return True
        try:
            return page.locator(marker).first.is_visible(timeout=1500)
        except Exception:  # noqa: BLE001
            return False

    def begin_sign_in(self) -> Dict[str, Any]:
        """Open a visible window at the system for the user to sign in."""
        try:
            self.require_enabled()
        except NotConfigured as exc:
            return {"ok": False, "status": "not_configured", "detail": str(exc)}

        browser = engine()

        def open_window(eng):
            eng.hold_visible = True
            eng.close_context()
            page = eng.page(self.name, mode="visible")
            page.goto(self.sign_in_url(), wait_until="domcontentloaded")
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
            return page.url

        try:
            browser.submit(open_window, f"Opening {self.label} sign-in")
        except Exception as exc:  # noqa: BLE001 - shown to the user
            browser.hold_visible = False
            return {"ok": False, "status": "failed", "detail": str(exc)}

        with self._state_lock:
            self._sign_in = {"status": "waiting", "started": time.time(),
                             "detail": f"Sign in to {self.label} in the window that opened."}
        threading.Thread(target=self._watch_sign_in, daemon=True,
                         name=f"cerebro-signin-{self.name}").start()
        return {"ok": True, **self.sign_in_state()}

    def _watch_sign_in(self) -> None:
        browser = engine()

        def probe(eng):
            page = eng._pages.get(self.name)
            if page is None or page.is_closed():
                return "closed"
            return "signed_in" if self.is_signed_in(page) else "waiting"

        deadline = time.time() + SIGN_IN_TIMEOUT_SECONDS
        outcome = "timeout"
        while time.time() < deadline:
            time.sleep(SIGN_IN_POLL_SECONDS)
            try:
                result = browser.submit(probe, f"Waiting for {self.label} sign-in", timeout=30)
            except Exception as exc:  # noqa: BLE001 - window closed under us, etc.
                logger.warn("browser", "Sign-in probe failed", {"error": str(exc)})
                result = "closed"
            if result != "waiting":
                outcome = result
                break

        def finish(eng):
            eng.hold_visible = False
            eng.close_context()

        try:
            browser.submit(finish, f"Finishing {self.label} sign-in", timeout=30)
        except Exception:  # noqa: BLE001
            browser.hold_visible = False

        with self._state_lock:
            if outcome == "signed_in":
                self._signed_in, self._checked_at = True, time.time()
                self._sign_in = {"status": "connected", "ok": True,
                                 "detail": f"Signed in to {self.label}."}
            elif outcome == "closed":
                self._sign_in = {"status": "cancelled", "ok": False,
                                 "detail": "The sign-in window was closed before signing in."}
            else:
                self._sign_in = {"status": "failed", "ok": False,
                                 "detail": "Sign-in did not finish within 10 minutes."}
        logger.info("browser", "Sign-in finished", {"connector": self.name, "outcome": outcome})

    def sign_in_state(self) -> Dict[str, Any]:
        with self._state_lock:
            return dict(self._sign_in)

    def check(self) -> Dict[str, Any]:
        """Open the system hidden and report whether the session still works."""
        try:
            self.require_enabled()
            signed_in, account = engine().submit(
                lambda eng: self._probe_signed_in(eng), f"Checking {self.label} sign-in")
        except (NotConfigured, BrowserUnavailable) as exc:
            return {"ok": False, "signed_in": False, "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "signed_in": False, "detail": f"Could not open {self.label}: {exc}"}
        with self._state_lock:
            self._signed_in, self._checked_at = signed_in, time.time()
            self._account = account if signed_in else None
        detail = (f"Signed in to {self.label}" + (f" as {account}" if account else "")
                  if signed_in else f"Not signed in — click Sign in for {self.label}.")
        return {"ok": signed_in, "signed_in": signed_in, "account": account, "detail": detail}

    def _probe_signed_in(self, eng):
        page = eng.page(self.name)
        page.goto(self.sign_in_url(), wait_until="domcontentloaded")
        self.settle(page)
        if not self.is_signed_in(page):
            return False, None
        try:
            return True, self.account_name(page)
        except SignInRequired:
            return False, None
        except Exception as exc:  # noqa: BLE001 - signed in; the name is a nicety
            logger.info("browser", "Could not read the signed-in account",
                        {"connector": self.name, "error": str(exc)[:200]})
            return True, None

    def account_name(self, page) -> Optional[str]:
        """Who the browser is signed in as, when the system can say (override)."""
        return None

    def sign_out(self) -> Dict[str, Any]:
        """Forget this system's cookies in Cerebro's browser profile."""
        host = urlparse(self.base_url).hostname or ""

        def clear(eng):
            context = eng.context()
            cookies = [c for c in context.cookies()
                       if host and (c.get("domain") or "").lstrip(".").endswith(
                           host.split(".", 1)[-1])]
            for cookie in cookies:
                try:
                    context.clear_cookies(name=cookie["name"], domain=cookie["domain"])
                except TypeError:  # older Playwright: no filters
                    context.clear_cookies()
                    break
            eng._save_session()
            return len(cookies)

        try:
            removed = engine().submit(clear, f"Signing out of {self.label}")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}
        with self._state_lock:
            self._signed_in, self._checked_at = False, time.time()
            self._sign_in = {"status": "idle"}
        return {"ok": True, "detail": f"Signed out of {self.label} ({removed} cookie(s) removed)."}

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            signed_in, checked = self._signed_in, self._checked_at
            sign_in = dict(self._sign_in)
        return {
            "name": self.name, "label": self.label,
            "enabled": self.enabled, "url": self.base_url or None,
            "configured": bool(self.base_url),
            "signed_in": signed_in, "checked_at": checked,
            "account": self._account if signed_in else None,
            "sign_in": sign_in,
            "can_auto_apply": bool(self.auto_apply_setting),
            "auto_apply": self.auto_apply,
        }

    @property
    def auto_apply(self) -> bool:
        """Whether changes here are made without waiting for approval."""
        return bool(self.auto_apply_setting and getattr(settings, self.auto_apply_setting, False))

    # -------------------------------------------------------- page work
    def run(self, fn: Callable[[Any], Any], label: str) -> Any:
        """Run ``fn(page)`` on this connector's tab in the hidden browser.

        Raises :class:`SignInRequired` when the site sends the browser to a
        sign-in page, so callers can offer the "Sign in" button.
        """
        self.require_enabled()

        def job(eng):
            page = eng.page(self.name)
            return fn(page)

        try:
            result = engine().submit(job, label)
        except SignInRequired:
            with self._state_lock:
                self._signed_in, self._checked_at = False, time.time()
            raise
        with self._state_lock:
            self._signed_in, self._checked_at = True, time.time()
        return result

    def settle(self, page, timeout_ms: int = 8000) -> None:
        """Wait for the page to stop loading, without failing if it never does."""
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - busy pages never go idle; that's fine
            pass

    def goto(self, page, path: str = "") -> None:
        """Navigate on this system, raising SignInRequired on a login page."""
        page.goto(self.url(path), wait_until="domcontentloaded")
        self.settle(page)
        if self.looks_like_login(page.url) or not self.on_own_site(page.url):
            raise SignInRequired(self)

    def ensure_on_site(self, page) -> None:
        """Be on this system's origin — required before :meth:`fetch`."""
        if not self.on_own_site(page.url) or self.looks_like_login(page.url):
            self.goto(page, self.sign_in_url())

    def fetch(self, page, path: str, method: str = "GET", body: Any = None,
              headers: Dict[str, str] = None) -> Dict[str, Any]:
        """An HTTP request made *by the page*, with the user's session.

        This is how connectors use a system's own API without credentials:
        the request carries the browser's cookies exactly as the system's own
        web app does.
        """
        self.ensure_on_site(page)
        result = page.evaluate(_FETCH_JS, {"url": self.url(path), "method": method,
                                           "body": body, "headers": headers or {}})
        if result.get("status") in (401, 403) or self.looks_like_login(result.get("url") or ""):
            raise SignInRequired(self)
        return result

    def readable_text(self, page, selector: str = None) -> str:
        """The page's main readable text (navigation, scripts and chrome removed)."""
        return page.evaluate(_READABLE_JS, selector or None) or ""

    @staticmethod
    def readable_text_of(locator) -> str:
        """Readable text of one element found with any Playwright selector."""
        try:
            return locator.evaluate(_READABLE_ELEMENT_JS) or ""
        except Exception:  # noqa: BLE001
            return ""
