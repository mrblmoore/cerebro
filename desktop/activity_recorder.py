#!/usr/bin/env python3
"""
Cerebro activity recorder — the eyes and hands of the second brain.

Periodically captures a downscaled screenshot of the active window and, if
enabled, the text you type, and sends both to Cerebro. Cerebro redacts secrets
before storing anything, and refuses sensitive windows outright — but this
recorder also does its own front-line filtering so a password never leaves the
machine even in transit.

It is off unless capture is switched on in Cerebro's settings, and it reads that
setting live, so turning capture off stops it within one poll.

Screenshot and window-title capture are Windows-only: they rely on the Win32
foreground-window APIs, and without a window title the sensitive-window and
excluded-app filters cannot screen what they capture — so on macOS and Linux the
recorder deliberately captures nothing rather than capture unscreened content.

    python cerebro.py capture          (recommended)
    python desktop/activity_recorder.py

Dependencies are optional and degrade gracefully:
    pip install -r desktop/requirements-capture.txt
"""

import argparse
import base64
import io
import os
import sys
import threading
import time
from typing import List, Optional

import requests

DEFAULT_API = os.environ.get("CEREBRO_API_URL", "http://127.0.0.1:8000")
IS_WINDOWS = sys.platform == "win32"

try:
    import mss
    from PIL import Image
    SCREENSHOTS_AVAILABLE = True
except Exception:
    SCREENSHOTS_AVAILABLE = False

try:
    from pynput import keyboard
    KEYBOARD_AVAILABLE = True
except Exception:
    KEYBOARD_AVAILABLE = False

#: Front-line window filter, mirroring the backend's looks_sensitive. Belt and
#: braces: the recorder should not even transmit a login window's screenshot.
SENSITIVE_MARKERS = (
    "password", "sign in", "log in", "login", "credential", "authenticator",
    "1password", "lastpass", "bitwarden", "keepass", "keeper", "bank",
    "banking", "wallet", "paypal", "checkout", "payment", "incognito",
    "private browsing",
)


def active_window_rect() -> "dict | None":
    """Bounding box of the foreground window, for a window-only screenshot."""
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return {"left": rect.left, "top": rect.top, "width": width, "height": height}


def active_window() -> "tuple | None":
    """
    (process_name, window_title) for the foreground window.

    Returns None when the active window cannot be determined. That distinction
    matters for safety: without a window title the sensitive-window and
    excluded-app filters are blind, so the recorder must not capture at all
    rather than capture something it could not screen.
    """
    if IS_WINDOWS:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            return None
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return "", buffer.value
    return None


def is_sensitive(title: str, application: str) -> bool:
    haystack = f"{title or ''} {application or ''}".lower()
    return any(marker in haystack for marker in SENSITIVE_MARKERS)


class ActivityRecorder:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.config = {}
        self._typed: List[str] = []
        self._typed_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_window = None
        self._keyboard_started = False

    # ---------------------------------------------------------- transport
    def refresh_config(self) -> dict:
        try:
            response = self.session.get(f"{self.api_url}/api/activity/config", timeout=5)
            response.raise_for_status()
            self.config = response.json()
        except requests.RequestException:
            self.config = {}
        return self.config

    def send(self, **frame) -> None:
        try:
            self.session.post(f"{self.api_url}/api/activity/frame", json=frame, timeout=15)
        except requests.RequestException as exc:
            print(f"  ! could not send frame: {exc}")

    def wait_for_api(self) -> None:
        announced = False
        while not self._stop.is_set():
            try:
                self.session.get(f"{self.api_url}/health", timeout=4).raise_for_status()
                if announced:
                    print("Connected.")
                return
            except requests.RequestException:
                if not announced:
                    print(f"Waiting for Cerebro at {self.api_url}…")
                    announced = True
                time.sleep(3)

    # ---------------------------------------------------------- keyboard
    def _on_key(self, key) -> None:
        if not self.config.get("enabled") or not self.config.get("keystrokes"):
            return
        try:
            character = key.char
        except AttributeError:
            # Space and Enter delimit words; other control keys are ignored so
            # the buffer holds words, not a raw keylog.
            if key in (keyboard.Key.space, keyboard.Key.enter):
                character = " "
            else:
                return
        if character:
            with self._typed_lock:
                self._typed.append(character)

    def _drain_typed(self) -> str:
        with self._typed_lock:
            text = "".join(self._typed).strip()
            self._typed.clear()
        return text

    def _start_keyboard(self) -> None:
        if not KEYBOARD_AVAILABLE:
            print("  (typed-text capture unavailable — pip install pynput)")
            return
        listener = keyboard.Listener(on_press=self._on_key)
        listener.daemon = True
        listener.start()
        self._keyboard_started = True

    # -------------------------------------------------------- screenshots
    def _screenshot_b64(self, max_px: int) -> Optional[str]:
        if not SCREENSHOTS_AVAILABLE:
            return None
        # Grab only the active window's rectangle. sct.monitors[0] is the union
        # of every monitor, which would sweep in content from other screens the
        # sensitive-window check never saw. Without a rectangle we capture
        # nothing rather than fall back to the whole desktop.
        region = active_window_rect()
        if region is None:
            return None
        try:
            with mss.mss() as sct:
                shot = sct.grab(region)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            return None

        # Downscale hard: this is for recognising which app, not reading text.
        image.thumbnail((max_px, max_px))
        buffer = io.BytesIO()
        image.convert("L" if max_px <= 640 else "RGB").save(
            buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    # --------------------------------------------------------------- loop
    def run(self) -> int:
        print(f"Cerebro activity recorder → {self.api_url}")
        if not SCREENSHOTS_AVAILABLE:
            print("  (screenshots unavailable — pip install -r desktop/requirements-capture.txt)")
        self.wait_for_api()
        self.refresh_config()

        if not self.config.get("enabled"):
            print("\n  Activity capture is switched OFF in Cerebro.")
            print("  Turn it on in Settings → Activity capture, then restart this.\n")

        print("Recording while capture is enabled. Press Ctrl+C to stop.\n")

        last_shot = 0.0
        last_flush = time.time()
        try:
            while not self._stop.is_set():
                self.refresh_config()
                if not self.config.get("enabled"):
                    self._drain_typed()
                    time.sleep(5)
                    continue

                if self.config.get("keystrokes") and not self._keyboard_started:
                    self._start_keyboard()

                window = active_window()
                if window is None:
                    # Unknown window: cannot screen it, so capture nothing and
                    # discard anything typed against it.
                    self._drain_typed()
                    time.sleep(2)
                    continue
                application, title = window
                if is_sensitive(title, application):
                    # Do not even flush typed text captured against a login box.
                    self._drain_typed()
                    time.sleep(2)
                    continue

                # Typed text, flushed when the window changes or every ~90s, so a
                # long-lived window still gets sent in reasonable chunks.
                if self.config.get("keystrokes"):
                    due = title != self._last_window or time.time() - last_flush > 90
                    if due:
                        typed = self._drain_typed()
                        if len(typed) >= 8:
                            self.send(kind="keystrokes", application=application,
                                      window_title=self._last_window or title, text=typed)
                        last_flush = time.time()
                self._last_window = title

                # Screenshots on their own cadence.
                interval = max(15, int(self.config.get("screenshot_seconds", 60)))
                if self.config.get("screenshots") and time.time() - last_shot >= interval:
                    shot = self._screenshot_b64(int(self.config.get("screenshot_max_px", 1280)))
                    if shot:
                        self.send(kind="screenshot", application=application,
                                  window_title=title, screenshot_b64=shot)
                        print(f"  → screenshot · {title[:48]}")
                    last_shot = time.time()

                time.sleep(2)
        except KeyboardInterrupt:
            print("\nRecorder stopped.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cerebro activity recorder")
    parser.add_argument("--api", default=DEFAULT_API, help="Cerebro API base URL")
    arguments = parser.parse_args()
    return ActivityRecorder(arguments.api).run()


if __name__ == "__main__":
    sys.exit(main())
