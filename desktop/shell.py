#!/usr/bin/env python3
"""
Cerebro's desktop shell: the app window plus the tray brain.

The window is a frameless pywebview window (Microsoft Edge WebView2 on
Windows) showing the backend's ``/app`` page, so the desktop and the browser
share one modern interface. Closing it **hides it to the tray** — Cerebro keeps
running, its brain animating to show what it is doing. "Quit Cerebro" in the
tray menu is the only thing that stops it, and it stops the server too.

    python desktop/shell.py                 # open the window
    python desktop/shell.py --background    # start in the tray (sign-in start)
    python desktop/shell.py --classic       # the previous Tkinter widget

If pywebview (or WebView2) is not available the classic widget opens instead.
"""

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import widget_config  # noqa: E402

COMPACT_HEIGHT = 74
DEFAULT_SIZE = (440, 760)
SHOW_REQUEST = widget_config.config_dir() / "show.request"
LOCK_PATH = widget_config.config_dir() / "shell.lock"


# --------------------------------------------------------- single instance
class SingleInstance:
    """Holds an exclusive lock for as long as this shell runs.

    A second launch cannot take the lock, so instead it leaves a "show me"
    request for the running shell (picked up within a second) and exits —
    double-clicking Cerebro while it sits in the tray simply brings it back.
    """

    def __init__(self):
        self._handle = None

    def acquire(self) -> bool:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = open(LOCK_PATH, "a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    @staticmethod
    def ask_running_instance_to_show(tab: str = "") -> None:
        try:
            SHOW_REQUEST.parent.mkdir(parents=True, exist_ok=True)
            SHOW_REQUEST.write_text(tab or "show", encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------- JS bridge
class ShellApi:
    """Methods the page calls as ``window.pywebview.api.<name>()``."""

    def __init__(self, shell: "Shell"):
        self._shell = shell

    def hide(self):
        self._shell.hide()

    def set_compact(self, compact):
        self._shell.set_compact(bool(compact))
        return bool(compact)

    def toggle_on_top(self):
        return self._shell.toggle_on_top()

    def open_external(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def quit(self):
        self._shell.quit()


# ----------------------------------------------------------------- shell
class Shell:
    def __init__(self, api_url: str, start_hidden: bool = False):
        self.api_url = api_url.rstrip("/")
        self.config = widget_config.load()
        self.start_hidden = start_hidden
        self.window = None
        self.tray = None
        self.compact = False
        self._quitting = False
        self._save_timer = None
        self._full_height = None

    # -------------------------------------------------------- window
    def create_window(self):
        import webview

        width = int(self.config.get("width") or 0)
        height = int(self.config.get("height") or 0)
        if width < 360 or height < 480:      # sizes from the old, smaller widget
            width, height = DEFAULT_SIZE
        self.window = webview.create_window(
            "Cerebro", url=f"{self.api_url}/app?shell=1", js_api=ShellApi(self),
            width=width, height=height,
            x=self.config.get("x"), y=self.config.get("y"),
            min_size=(360, COMPACT_HEIGHT), frameless=True, easy_drag=False,
            on_top=bool(self.config.get("always_on_top", True)),
            background_color="#07080E", hidden=self.start_hidden, text_select=True,
        )
        events = self.window.events
        events.closing += self._on_closing
        events.moved += self._on_moved
        events.resized += self._on_resized
        return self.window

    def _on_closing(self):
        # Closing the window (Alt+F4, taskbar) hides it; only Quit stops Cerebro.
        if self._quitting:
            return True
        self.hide()
        return False

    def _on_moved(self, x, y):
        self.config["x"], self.config["y"] = int(x), int(y)
        self._save_soon()

    def _on_resized(self, width, height):
        if not self.compact and height > COMPACT_HEIGHT + 20:
            self.config["width"], self.config["height"] = int(width), int(height)
            self._save_soon()

    def _save_soon(self):
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.8, widget_config.save, args=(self.config,))
        self._save_timer.daemon = True
        self._save_timer.start()

    def show(self, tab: str = None):
        if self.window is None:
            return
        self.window.show()
        try:
            self.window.restore()
        except Exception:  # noqa: BLE001 - not minimised
            pass
        if tab:
            self.window.evaluate_js(f"window.cerebroSelectTab && window.cerebroSelectTab({tab!r})")

    def hide(self):
        if self.window is not None:
            self.window.hide()

    def set_compact(self, compact: bool):
        self.compact = compact
        width = int(self.config.get("width") or DEFAULT_SIZE[0])
        if compact:
            self._full_height = int(self.config.get("height") or DEFAULT_SIZE[1])
            self.window.resize(width, COMPACT_HEIGHT)
        else:
            self.window.resize(width, self._full_height or DEFAULT_SIZE[1])

    def toggle_on_top(self) -> bool:
        value = not bool(self.config.get("always_on_top", True))
        self.config["always_on_top"] = value
        self.window.on_top = value
        widget_config.save(self.config)
        return value

    # ---------------------------------------------------------- tray
    def start_tray(self):
        try:
            import tray as tray_module
        except ImportError:
            return None
        try:
            import win_integration as win
            startup = {"get": win.startup_enabled,
                       "set": lambda enabled: win.set_startup(enabled, _project_root())} \
                if win.IS_WINDOWS else None
        except ImportError:
            startup = None
        actions = {
            "open": lambda tab=None: self.show(tab),
            "dashboard": lambda: webbrowser.open(f"{self.api_url}/"),
            "settings": lambda: webbrowser.open(f"{self.api_url}/settings"),
            "quit": self.quit,
            "tick": self._check_show_request,
        }
        try:
            tray_module.preload()
            self.tray = tray_module.BrainTray(self.api_url, actions, startup)
            self.tray.start()
        except Exception as exc:  # noqa: BLE001 - no tray: closing then really quits
            print(f"Tray unavailable: {exc}")
            self.tray = None
        return self.tray

    def _check_show_request(self):
        try:
            if SHOW_REQUEST.exists():
                tab = SHOW_REQUEST.read_text(encoding="utf-8").strip()
                SHOW_REQUEST.unlink(missing_ok=True)
                self.show(None if tab == "show" else tab)
        except OSError:
            pass

    # ---------------------------------------------------------- quit
    def quit(self):
        """Stop everything: the window, the tray, and the local server."""
        self._quitting = True
        try:
            import requests

            requests.post(f"{self.api_url}/api/system/shutdown", timeout=3)
        except Exception:  # noqa: BLE001 - already stopped is fine
            pass
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    def run(self):
        import webview

        self.create_window()
        tray = self.start_tray()
        if tray is None and self.start_hidden:
            self.window.show()   # no tray to bring it back from: never start hidden
        if tray is None:
            self.window.events.closing -= self._on_closing
        storage = widget_config.config_dir() / "webview"
        webview.start(gui="edgechromium" if sys.platform == "win32" else None,
                      private_mode=False, storage_path=str(storage))
        if self.tray is not None:
            self.tray.stop()


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _wait_for_server(api_url: str, seconds: float = 20) -> bool:
    import requests

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if requests.get(f"{api_url}/health", timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def _run_classic(api_url: str) -> int:
    import widget

    sys.argv = [sys.argv[0], "--api", api_url]
    return widget.main()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cerebro desktop app")
    parser.add_argument("--api", help="Cerebro API base URL")
    parser.add_argument("--background", action="store_true",
                        help="start in the system tray without opening the window")
    parser.add_argument("--classic", action="store_true",
                        help="use the previous Tkinter widget")
    options = parser.parse_args(argv)

    config = widget_config.load()
    api_url = (options.api or config.get("api_url") or "http://127.0.0.1:8000").rstrip("/")

    if options.classic:
        return _run_classic(api_url)

    try:
        import webview  # noqa: F401
    except ImportError:
        print("pywebview is not installed; opening the classic widget.")
        return _run_classic(api_url)

    instance = SingleInstance()
    if not instance.acquire():
        SingleInstance.ask_running_instance_to_show()
        return 0

    if os.environ.get("CEREBRO_HELPERS_STARTED") != "1":
        import widget

        widget._start_desktop_helpers(api_url)
        os.environ["CEREBRO_HELPERS_STARTED"] = "1"

    _wait_for_server(api_url)
    try:
        Shell(api_url, start_hidden=options.background).run()
    except Exception as exc:  # noqa: BLE001 - e.g. WebView2 runtime missing
        print(f"The app window could not start ({exc}); opening the classic widget.")
        return _run_classic(api_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
