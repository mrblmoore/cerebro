"""
Connector base for systems Cerebro reaches through their own web API
(BeyondTrust Remote Support, Genesys Cloud).

The browser connectors borrow the user's sign-in; these two systems have a
proper API, which is far more reliable than driving their consoles, and both
authenticate with an OAuth *client credentials* pair that an administrator
creates once. The pair lives in the settings store (the secret is masked like
every other key) and is exchanged for a short-lived bearer token, cached here
until it nearly expires.

A connector exposes the same ``status()`` / ``check()`` shape as the browser
connectors, so it shows up on the Connect tab beside them.
"""

import threading
import time
from typing import Any, Dict, Optional

import requests

from app.core import logger
from app.core.config import settings

TOKEN_SAFETY_SECONDS = 60
REQUEST_TIMEOUT = 30


class SystemCallError(RuntimeError):
    """A call to the system failed in a way worth explaining to the user."""


class NotConfigured(RuntimeError):
    """The connector is switched off or missing its address or credentials."""


class ApiConnector:
    name = ""
    kind = "api"

    #: Settings that must all be filled in, as ``(attribute, human label)``.
    required = ()

    def __init__(self):
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._ok: Optional[bool] = None
        self._checked_at: Optional[float] = None
        self._detail: str = ""

    # --------------------------------------------------------- settings
    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, self.enabled_setting, False))

    def missing(self):
        return [label for attribute, label in self.required
                if not str(getattr(settings, attribute, None) or "").strip()]

    def require_enabled(self) -> None:
        if not self.enabled:
            raise NotConfigured(f"{self.label} is switched off. Turn it on in the Connect tab.")
        missing = self.missing()
        if missing:
            raise NotConfigured(f"{self.label} needs: {', '.join(missing)} (see Settings).")

    # ------------------------------------------------------------ auth
    def api_base(self) -> str:
        raise NotImplementedError

    def token_url(self) -> str:
        raise NotImplementedError

    def credentials(self):
        raise NotImplementedError

    def bearer(self, force: bool = False) -> str:
        """A valid access token, fetching a fresh one when needed."""
        self.require_enabled()
        with self._lock:
            if not force and self._token and time.time() < self._expires_at:
                return self._token
            client_id, client_secret = self.credentials()
            try:
                response = requests.post(
                    self.token_url(), data={"grant_type": "client_credentials"},
                    auth=(client_id, client_secret), timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                raise SystemCallError(f"Could not reach {self.label}: {exc}") from exc
            if response.status_code in (400, 401, 403):
                raise SystemCallError(
                    f"{self.label} rejected the client ID and secret "
                    f"(HTTP {response.status_code}). Check them in Settings.")
            if not response.ok:
                raise SystemCallError(f"{self.label} sign-in failed (HTTP {response.status_code}).")
            body = response.json()
            self._token = body.get("access_token")
            if not self._token:
                raise SystemCallError(f"{self.label} sign-in returned no token.")
            self._expires_at = time.time() + max(
                30, int(body.get("expires_in") or 3600) - TOKEN_SAFETY_SECONDS)
            return self._token

    def forget_token(self) -> None:
        with self._lock:
            self._token, self._expires_at = None, 0.0

    # ------------------------------------------------------------ http
    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """An authorised call; retries once with a new token if the old expired."""
        url = path if path.startswith("http") else f"{self.api_base()}{path}"
        extra_headers = kwargs.pop("headers", {})
        response = None
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.bearer(force=bool(attempt))}",
                       **extra_headers}
            try:
                response = requests.request(method, url, headers=headers,
                                            timeout=REQUEST_TIMEOUT, **kwargs)
            except requests.RequestException as exc:
                raise SystemCallError(f"Could not reach {self.label}: {exc}") from exc
            if response.status_code == 401 and attempt == 0:
                self.forget_token()
                continue
            break
        if response.status_code == 403:
            raise SystemCallError(f"{self.label} refused this request: the API account is "
                                  "missing a permission for it.")
        if response.status_code == 429:
            raise SystemCallError(f"{self.label} is rate-limiting Cerebro; try again shortly.")
        if not response.ok:
            raise SystemCallError(f"{self.label} returned HTTP {response.status_code}: "
                                  f"{(response.text or '')[:200]}")
        return response

    # ---------------------------------------------------------- status
    def verify(self) -> str:
        """Make one cheap authorised call; return a short description."""
        raise NotImplementedError

    def check(self) -> Dict[str, Any]:
        try:
            self.require_enabled()
            self.forget_token()
            detail = self.verify()
            ok = True
        except (NotConfigured, SystemCallError) as exc:
            ok, detail = False, str(exc)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            logger.warn("systems", "Connection check failed",
                        {"system": self.name, "error": str(exc)})
            ok, detail = False, f"Could not connect to {self.label}: {exc}"
        with self._lock:
            self._ok, self._checked_at, self._detail = ok, time.time(), detail
        return {"ok": ok, "signed_in": ok, "detail": detail}

    def display_address(self) -> str:
        return ""

    def status(self) -> Dict[str, Any]:
        with self._lock:
            ok, checked, detail = self._ok, self._checked_at, self._detail
        return {
            "name": self.name, "label": self.label, "kind": "api",
            "enabled": self.enabled,
            "url": self.display_address() or None,
            "configured": not self.missing(),
            "missing": self.missing(),
            "signed_in": ok, "checked_at": checked, "account": None,
            "sign_in": {"status": "connected" if ok else "idle", "detail": detail},
            "can_auto_apply": False, "auto_apply": False,
        }

    def sign_in_state(self) -> Dict[str, Any]:
        return self.status()["sign_in"]

    def begin_sign_in(self) -> Dict[str, Any]:
        """There is no window to open: check the credentials right now."""
        result = self.check()
        return {"ok": result["ok"], "status": "connected" if result["ok"] else "failed",
                "detail": result["detail"]}

    def sign_out(self) -> Dict[str, Any]:
        self.forget_token()
        with self._lock:
            self._ok, self._detail = False, ""
        return {"ok": True, "detail": f"Cerebro forgot its {self.label} access token."}
