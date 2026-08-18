#!/usr/bin/env python3
"""
Cerebro desktop agent — reports the active window to Cerebro.

The widget shows context; this agent *produces* some of it. On Windows it reads
the foreground window title and process, matches it against the same rules the
backend uses, and posts an event when something meaningful changes: a Teams call
starting, a Bomgar session connecting, a switch to a different application.

Run it alongside the widget:

    python cerebro.py start        # in one terminal
    python desktop/agent.py        # in another

On macOS and Linux there is no foreground-window API here, so the agent runs in
"heartbeat only" mode and simply verifies the connection — the browser extension
remains the main event source there.
"""

import argparse
import os
import sys
import time
from typing import Optional, Tuple

import requests

DEFAULT_API = os.environ.get("CEREBRO_API_URL", "http://127.0.0.1:8000")
POLL_SECONDS = 2.0

IS_WINDOWS = sys.platform == "win32"

#: Processes that count as "still in a call" / "still in a remote session".
#: Focus leaving all of them is what ends the corresponding state.
CALL_APPS = ("teams", "zoom", "webex", "slack")
REMOTE_APPS = ("bomgar", "mstsc", "teamviewer", "anydesk", "vncviewer")


# --------------------------------------------------------------- detection
def active_window() -> Optional[Tuple[str, str]]:
    """Return ``(process_name, window_title)`` for the foreground window."""
    if not IS_WINDOWS:
        return None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    handle = user32.GetForegroundWindow()
    if not handle:
        return None

    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    title = buffer.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))

    process_name = ""
    # PROCESS_QUERY_LIMITED_INFORMATION — enough for the image name, and it
    # works without elevation on processes owned by the same user.
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    if process:
        try:
            size = wintypes.DWORD(260)
            name = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(size)):
                process_name = os.path.basename(name.value)
        finally:
            kernel32.CloseHandle(process)

    return process_name, title


def classify(process_name: str, title: str) -> Optional[Tuple[str, dict]]:
    """Map a window to a Cerebro event, or None when it is not interesting."""
    process = (process_name or "").lower()
    lowered = (title or "").lower()

    if "teams" in process or "teams" in lowered:
        if any(word in lowered for word in ("call", "meeting", "| microsoft teams")):
            if "call" in lowered or "meeting" in lowered:
                return "CALL_STARTED", {"application": "Microsoft Teams", "title": title}

    if "zoom" in process and ("meeting" in lowered or "zoom meeting" in lowered):
        return "CALL_STARTED", {"application": "Zoom", "title": title}

    if "bomgar" in process or "remote support" in lowered or "mstsc" in process:
        if "connected" in lowered or "session" in lowered:
            host = title.split("-")[-1].strip() if "-" in title else None
            return "REMOTE_SESSION_CONNECTED", {"host": host, "title": title}

    return None


# ------------------------------------------------------------------ agent
class DesktopAgent:
    def __init__(self, api_url: str = DEFAULT_API, interval: float = POLL_SECONDS):
        self.api_url = api_url.rstrip("/")
        self.interval = interval
        self.session = requests.Session()
        self.last_window = None
        self.call_active = False
        self.remote_active = False

    # ------------------------------------------------------------ network
    def post_event(self, event_type: str, data: dict) -> bool:
        try:
            response = self.session.post(
                f"{self.api_url}/api/events/",
                json={"event_type": event_type, "source": "desktop_agent", "data": data},
                timeout=6,
            )
            response.raise_for_status()
            print(f"  → {event_type}: {data.get('application') or data.get('title', '')[:50]}")
            return True
        except requests.RequestException as exc:
            print(f"  ! could not report {event_type}: {exc}")
            return False

    def context(self) -> dict:
        try:
            response = self.session.get(f"{self.api_url}/api/context/current", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return {}

    def wait_for_api(self) -> None:
        printed = False
        while True:
            try:
                self.session.get(f"{self.api_url}/health", timeout=4).raise_for_status()
                if printed:
                    print("Connected.")
                return
            except requests.RequestException:
                if not printed:
                    print(f"Waiting for Cerebro at {self.api_url} — start it with "
                          f"'python cerebro.py start'.")
                    printed = True
                time.sleep(3)

    # --------------------------------------------------------------- loop
    def tick(self) -> None:
        window = active_window()
        if not window:
            return

        process_name, title = window
        if not title or (process_name, title) == self.last_window:
            return
        self.last_window = (process_name, title)

        process = (process_name or "").lower()
        classified = classify(process_name, title)

        if classified:
            event_type, data = classified
            if event_type == "CALL_STARTED":
                if self.call_active:
                    return
                self.call_active = True
            elif event_type == "REMOTE_SESSION_CONNECTED":
                if self.remote_active:
                    return
                self.remote_active = True
            self.post_event(event_type, data)
            return

        # Focus moved somewhere that is neither a call nor a remote session.
        # Close whichever of those we had open, so the context cannot get stuck
        # reporting a call or session that ended minutes ago.
        if self.call_active and not any(name in process for name in CALL_APPS):
            self.call_active = False
            self.post_event("CALL_ENDED", {"application": process_name})

        if self.remote_active and not any(name in process for name in REMOTE_APPS):
            self.remote_active = False
            self.post_event("REMOTE_SESSION_DISCONNECTED", {"application": process_name})

        self.post_event("APPLICATION_CHANGED", {
            "application": process_name or "Unknown",
            "title": title,
            "url": None,
        })

    def run(self) -> int:
        print(f"Cerebro desktop agent → {self.api_url}")
        self.wait_for_api()

        if not IS_WINDOWS:
            print("\nForeground-window tracking is Windows-only, so this agent has "
                  "nothing to report on this platform.")
            print("Use the browser extension for case detection, and the widget "
                  "(python cerebro.py widget) to see context.\n")
            context = self.context()
            print(f"Current case: {context.get('crm_case') or 'none'}")
            return 0

        print("Watching the foreground window. Press Ctrl+C to stop.\n")
        try:
            while True:
                self.tick()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\nAgent stopped.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cerebro desktop agent")
    parser.add_argument("--api", default=DEFAULT_API, help="Cerebro API base URL")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS,
                        help="seconds between window checks")
    arguments = parser.parse_args()
    return DesktopAgent(arguments.api, arguments.interval).run()


if __name__ == "__main__":
    sys.exit(main())
