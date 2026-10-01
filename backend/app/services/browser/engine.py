"""
The hidden browser.

Playwright's sync API is not thread-safe: a browser, its pages and every call
on them must stay on the thread that created them. FastAPI handles requests on
a pool of threads, so this module gives the browser **one worker thread of its
own**. Everything else hands it work with :meth:`BrowserEngine.submit` and
waits for the result.

The browser runs on a persistent profile in ``DATA_DIR/browser_profile`` —
Cerebro's own, separate from the user's everyday browser — so a sign-in done
once (see :mod:`app.services.browser.connector`) is reused until the site
expires it. It prefers the Microsoft Edge already installed on Windows, so no
browser has to be downloaded or bundled.

Modes (``BROWSER_MODE``):

``headless``   invisible; the default.
``offscreen``  a real window parked off screen, for sign-in systems that
               refuse headless browsers.
``visible``    a normal window; used for sign-in, and for watching a new
               setup work.
"""

import json
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.core import logger
from app.core.activity_state import activity
from app.core.config import settings
from app.core.paths import BROWSER_PROFILE_DIR, BROWSER_SCREENSHOTS_DIR

MODES = ("headless", "offscreen", "visible")
KEEP_SCREENSHOTS = 20


def _session_file():
    # Beside the profile (not inside it), so it survives a profile reset.
    return BROWSER_PROFILE_DIR.parent / "browser_session.bin"


def _protect(data: bytes) -> bytes:
    """Encrypt for the current Windows user (DPAPI); elsewhere, file permissions."""
    if sys.platform != "win32":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                         ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None,
                                                  None, 0, ctypes.byref(out)):
        raise OSError("Windows could not protect the browser session")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                         ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None,
                                                    None, 0, ctypes.byref(out)):
        raise OSError("Windows could not read the saved browser session")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class BrowserUnavailable(RuntimeError):
    """The browser could not be started (not installed, or switched off)."""


def playwright_installed() -> bool:
    from importlib import util as importlib_util

    try:
        return importlib_util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


class _Job:
    __slots__ = ("fn", "label", "future", "mode")

    def __init__(self, fn, label, mode):
        self.fn, self.label, self.mode = fn, label, mode
        self.future: Future = Future()


class BrowserEngine:
    """Owns the Playwright browser on a dedicated thread."""

    def __init__(self):
        self._jobs: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._playwright = None
        self._context = None
        self._pages: Dict[str, Any] = {}
        self._current_page = None
        self._mode: Optional[str] = None
        self._channel: Optional[str] = None
        self._last_used = 0.0
        #: While a sign-in window is open the browser stays visible and is
        #: never closed for being idle.
        self.hold_visible = False
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------ public
    def submit(self, fn: Callable[["BrowserEngine"], Any], label: str = "Working in the browser",
               mode: str = None, timeout: float = None) -> Any:
        """Run ``fn(engine)`` on the browser thread and return its result.

        ``fn`` may call :meth:`page`, :meth:`context` and friends — they are
        only valid inside a job. Exceptions are re-raised here.
        """
        if not settings.BROWSER_AUTOMATION_ENABLED:
            raise BrowserUnavailable(
                "The hidden browser is switched off. Turn it on in Settings → "
                "RightAnswers, Dynamics & SharePoint.")
        if not playwright_installed():
            raise BrowserUnavailable(
                "The browser automation component (Playwright) is not installed. "
                "Run: pip install -r backend/requirements-browser.txt")
        self._ensure_thread()
        job = _Job(fn, label, mode)
        self._jobs.put(job)
        wait = timeout or max(30, int(settings.BROWSER_TIMEOUT_SECONDS or 30) * 4)
        return job.future.result(timeout=wait)

    def status(self) -> Dict[str, Any]:
        return {
            "installed": playwright_installed(),
            "enabled": bool(settings.BROWSER_AUTOMATION_ENABLED),
            "running": self._context is not None,
            "mode": self._mode,
            "channel": self._channel,
            "configured_channel": settings.BROWSER_CHANNEL,
            "configured_mode": settings.BROWSER_MODE,
            "profile_dir": str(BROWSER_PROFILE_DIR),
            "signing_in": self.hold_visible,
            "last_error": self.last_error,
        }

    def shutdown(self) -> None:
        """Close the browser and stop the worker (app shutdown)."""
        if self._thread and self._thread.is_alive():
            self._jobs.put(None)
            self._thread.join(timeout=10)
        self._thread = None

    # ------------------------------------------------- inside a job only
    def context(self, mode: str = None):
        """The persistent browser context, launched or relaunched as needed."""
        wanted = mode or self._default_mode()
        if self.hold_visible:
            wanted = "visible"
        if self._context is not None and self._mode != wanted and mode is not None:
            self.close_context()
        if self._context is None:
            self._launch(wanted)
        return self._context

    def page(self, key: str, mode: str = None):
        """A tab reserved for ``key`` (usually a connector name)."""
        context = self.context(mode)
        page = self._pages.get(key)
        if page is None or page.is_closed():
            reusable = [p for p in context.pages if p not in self._pages.values()
                        and p.url in ("about:blank", "")]
            page = reusable[0] if reusable else context.new_page()
            self._pages[key] = page
        self._current_page = page
        return page

    def close_context(self) -> None:
        if self._context is not None:
            self._save_session()
            try:
                self._context.close()
            except Exception as exc:  # noqa: BLE001 - already gone is fine
                logger.warn("browser", "Closing the browser failed", {"error": str(exc)})
        self._context = None
        self._pages.clear()
        self._current_page = None
        self._mode = None

    # ------------------------------------------------------------ worker
    def _default_mode(self) -> str:
        mode = (settings.BROWSER_MODE or "headless").lower()
        return mode if mode in MODES else "headless"

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="cerebro-browser",
                                                daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=5)
            except queue.Empty:
                self._maybe_close_idle()
                continue
            if job is None:
                break
            if not job.future.set_running_or_notify_cancel():
                continue
            try:
                with activity("browsing", job.label):
                    if job.mode:
                        self.context(job.mode)
                    result = job.fn(self)
                job.future.set_result(result)
                self.last_error = None
            except BaseException as exc:  # noqa: BLE001 - handed back to the caller
                self.last_error = str(exc)[:300]
                self._screenshot(job.label)
                job.future.set_exception(exc)
            finally:
                self._last_used = time.time()
        self.close_context()
        self._stop_playwright()

    def _maybe_close_idle(self) -> None:
        if self._context is None or self.hold_visible:
            return
        idle = int(settings.BROWSER_IDLE_SECONDS or 300)
        if time.time() - self._last_used > idle:
            logger.info("browser", "Closing idle browser", {"idle_s": idle})
            self.close_context()
            self._stop_playwright()

    def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

    def _channels(self):
        configured = (settings.BROWSER_CHANNEL or "msedge").lower()
        order = [configured, "msedge", "chrome", "chromium"]
        seen = []
        for channel in order:
            if channel not in seen:
                seen.append(channel)
        return seen

    def _launch(self, mode: str) -> None:
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        headless = mode == "headless"
        args = ["--disable-features=Translate", "--no-first-run",
                "--no-default-browser-check"]
        if mode == "offscreen":
            args += ["--window-position=-32000,-32000", "--window-size=1366,900"]
        options = {
            "user_data_dir": str(BROWSER_PROFILE_DIR), "headless": headless,
            "args": args, "accept_downloads": False,
            "viewport": {"width": 1366, "height": 900} if headless else None,
        }

        failures = []
        for channel in self._channels():
            try:
                kwargs = dict(options)
                if channel != "chromium":
                    kwargs["channel"] = channel
                context = self._playwright.chromium.launch_persistent_context(**kwargs)
            except Exception as exc:  # noqa: BLE001 - try the next browser
                failures.append(f"{channel}: {str(exc).splitlines()[0][:160]}")
                continue
            self._context, self._mode, self._channel = context, mode, channel
            context.set_default_timeout(int(settings.BROWSER_TIMEOUT_SECONDS or 30) * 1000)
            self._restore_session()
            if headless:
                self._unmark_headless(context)
            logger.info("browser", "Browser started", {"channel": channel, "mode": mode})
            return
        raise BrowserUnavailable(
            "No browser could be started. Install Microsoft Edge or Chrome, or run "
            "`python -m playwright install chromium`. Details: " + "; ".join(failures))

    # Many sign-ins (Dynamics' own cookie, single sign-on without "stay
    # signed in") use *session* cookies, which a browser drops when it
    # closes — and this one closes whenever it is idle, and after the sign-in
    # window. Cookies are therefore saved on close and put back on launch,
    # encrypted for the Windows user, so one sign-in lasts until the site
    # itself ends the session.
    def _save_session(self) -> None:
        try:
            cookies = self._context.cookies()
            path = _session_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = _protect(json.dumps(cookies).encode("utf-8"))
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            if sys.platform != "win32":
                os.chmod(temporary, 0o600)
            temporary.replace(path)
        except Exception as exc:  # noqa: BLE001 - losing it only means signing in again
            logger.warn("browser", "Could not save the browser session", {"error": str(exc)})

    def _restore_session(self) -> None:
        path = _session_file()
        if not path.exists():
            return
        try:
            saved = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
            now = time.time()
            present = {(c["name"], c["domain"], c["path"]) for c in self._context.cookies()}
            missing = [c for c in saved
                       if (c["name"], c["domain"], c["path"]) not in present
                       and (c.get("expires", -1) in (-1, None) or c["expires"] > now)]
            if missing:
                self._context.add_cookies(missing)
        except Exception as exc:  # noqa: BLE001
            logger.warn("browser", "Could not restore the browser session", {"error": str(exc)})

    def forget_session(self) -> None:
        """Delete the saved cookies (used when signing out)."""
        try:
            _session_file().unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _unmark_headless(context) -> None:
        """Some sign-in pages refuse a user agent that says "Headless"."""
        try:
            page = context.pages[0] if context.pages else context.new_page()
            agent = page.evaluate("navigator.userAgent") or ""
            if "Headless" in agent:
                context.set_extra_http_headers({"User-Agent": agent.replace("Headless", "")})
        except Exception:  # noqa: BLE001 - cosmetic; never block a launch on it
            pass

    def _screenshot(self, label: str) -> None:
        page = self._current_page
        if page is None or page.is_closed():
            return
        try:
            BROWSER_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "step"
            path = BROWSER_SCREENSHOTS_DIR / f"{stamp}-{slug}.png"
            page.screenshot(path=str(path), timeout=5000)
            logger.info("browser", "Saved failure screenshot", {"path": str(path)})
            shots = sorted(BROWSER_SCREENSHOTS_DIR.glob("*.png"))
            for old in shots[:-KEEP_SCREENSHOTS]:
                old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - diagnostics must not mask the real error
            pass


_ENGINE: Optional[BrowserEngine] = None
_ENGINE_LOCK = threading.Lock()


def engine() -> BrowserEngine:
    """The process-wide browser engine."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = BrowserEngine()
        return _ENGINE


def shutdown() -> None:
    if _ENGINE is not None:
        _ENGINE.shutdown()
