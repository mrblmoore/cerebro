"""
Cerebro in the system tray.

The tray is what keeps Cerebro visibly alive after its window is closed. Its
icon is the brain mascot, animated to show what Cerebro is doing right now
(see :mod:`brain_frames`). It follows the backend's live activity stream
(``/api/system/activity/stream``), puts the current activity in the tooltip,
and pops a notification when a change is waiting for approval.

Built on pystray, which uses the native Windows notification area API.
"""

import json
import threading
from typing import Callable, Dict, Optional

import requests

import brain_frames

TOOLTIP_LIMIT = 120   # Windows truncates tray tooltips at 127 characters


class ActivityWatcher(threading.Thread):
    """Follows the backend's activity stream, reconnecting as needed."""

    def __init__(self, api_url: str, on_change: Callable[[dict], None]):
        super().__init__(name="cerebro-tray-activity", daemon=True)
        self.api_url = api_url.rstrip("/")
        self.on_change = on_change
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        failures = 0
        while not self._halt.is_set():
            try:
                with requests.get(f"{self.api_url}/api/system/activity/stream",
                                  stream=True, timeout=(5, 40)) as response:
                    response.raise_for_status()
                    failures = 0
                    for line in response.iter_lines(decode_unicode=True):
                        if self._halt.is_set():
                            return
                        if line and line.startswith("data: "):
                            self.on_change(json.loads(line[6:]))
            except (requests.RequestException, ValueError):
                failures += 1
                if failures >= 2:
                    self.on_change({"state": "offline",
                                    "detail": "Cerebro's server is not responding"})
            self._halt.wait(min(15, 1 + failures * 2))


class BrainTray:
    """The animated tray icon and its menu."""

    def __init__(self, api_url: str, actions: Dict[str, Callable[..., None]],
                 startup: Optional[Dict[str, Callable]] = None,
                 buddy: Optional[Dict[str, Callable]] = None):
        self.api_url = api_url.rstrip("/")
        self.actions = actions
        self.startup = startup or {}
        #: {"get": () -> bool, "set": (bool) -> None} for the desktop buddy.
        self.buddy = buddy or {}
        #: Also told about every activity snapshot (the desktop buddy).
        self.listeners = []
        self.state = "idle"
        self.detail = "Starting…"
        self.pending = 0
        self.icon = None
        self._stop = threading.Event()
        self._watcher = ActivityWatcher(self.api_url, self._on_activity)

    # ------------------------------------------------------------ menu
    def _menu(self):
        from pystray import Menu, MenuItem as Item

        def act(name, *args):
            return lambda icon, item: self.actions[name](*args)

        return Menu(
            Item("Open Cerebro", act("open"), default=True),
            Item("Ask Cerebro…", act("open", "ask")),
            Item(lambda item: self._status_text(), None, enabled=False),
            Item(lambda item: f"{self.pending} change(s) waiting for approval",
                 act("open", "activity"), visible=lambda item: self.pending > 0),
            Menu.SEPARATOR,
            Item("RightAnswers, Dynamics & SharePoint…", act("open", "connect")),
            Item("Dashboard", act("dashboard")),
            Item("Settings", act("settings")),
            Item("Show working buddy", self._toggle_buddy,
                 checked=lambda item: bool(self.buddy.get("get", lambda: False)()),
                 visible=bool(self.buddy)),
            Item("Start with Windows", self._toggle_startup,
                 checked=lambda item: bool(self.startup.get("get", lambda: False)()),
                 visible=bool(self.startup)),
            Menu.SEPARATOR,
            Item("Quit Cerebro", act("quit")),
        )

    def _status_text(self) -> str:
        label = brain_frames_label(self.state)
        return label if self.state == "idle" else f"{label}: {self.detail}"[:60]

    def _toggle_buddy(self, icon, item) -> None:
        current = bool(self.buddy.get("get", lambda: False)())
        self.buddy.get("set", lambda enabled: None)(not current)

    def _toggle_startup(self, icon, item) -> None:
        current = bool(self.startup.get("get", lambda: False)())
        self.startup.get("set", lambda enabled: None)(not current)

    # ------------------------------------------------------------ life
    def start(self) -> None:
        import pystray

        self.icon = pystray.Icon("cerebro", brain_frames.frames("idle")[0],
                                 "Cerebro", menu=self._menu())
        self.icon.run_detached()
        threading.Thread(target=self._animate, name="cerebro-tray-animate", daemon=True).start()
        self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._watcher.stop()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001 - already gone
                pass

    def notify(self, message: str, title: str = "Cerebro") -> None:
        try:
            if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", True):
                self.icon.notify(message, title)
        except Exception:  # noqa: BLE001 - notifications are a courtesy
            pass

    # ---------------------------------------------------------- updates
    def _on_activity(self, snapshot: dict) -> None:
        for listener in self.listeners:
            try:
                listener(snapshot)
            except Exception:  # noqa: BLE001 - a listener never breaks the tray
                pass
        previous_pending = self.pending
        self.state = snapshot.get("state") or "idle"
        self.detail = snapshot.get("detail") or brain_frames_label(self.state)
        self.pending = int(snapshot.get("pending_approvals") or 0)
        if self.icon is not None:
            tooltip = "Cerebro" if self.state == "idle" else f"Cerebro — {self.detail}"
            self.icon.title = tooltip[:TOOLTIP_LIMIT]
            try:
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass
        if self.pending > previous_pending:
            self.notify("A change is waiting for your approval. Open Cerebro to review it.")

    def _animate(self) -> None:
        index = 0
        current = None
        while not self._stop.is_set():
            state = self.state
            loop = brain_frames.frames(state)
            if state != current:
                current, index = state, 0
            try:
                self.icon.icon = loop[index % len(loop)]
            except Exception:  # noqa: BLE001 - the icon may be mid-teardown
                pass
            index += 1
            self._stop.wait(brain_frames.frame_seconds(state))
            check = self.actions.get("tick")
            if check:
                check()


def brain_frames_label(state: str) -> str:
    return {
        "idle": "Ready", "thinking": "Thinking", "searching": "Searching sources",
        "browsing": "Working in the browser", "writing": "Making a change",
        "awaiting_approval": "Waiting for your approval", "syncing": "Syncing",
        "listening": "Listening", "error": "Something needs attention",
        "offline": "Offline",
    }.get(state, "Ready")


def preload(states=brain_frames.STATES) -> None:
    """Render every animation in the background so the first switch is instant."""
    def work():
        for state in states:
            brain_frames.frames(state)
    threading.Thread(target=work, name="cerebro-tray-frames", daemon=True).start()

