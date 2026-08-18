#!/usr/bin/env python3
"""
Entry point for the packaged Cerebro widget executable.

Built windowed, so double-clicking it shows the widget and no console. It also
starts the packaged Cerebro server and bundled desktop helpers when needed.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent / "desktop", _HERE):
    if (candidate / "widget.py").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _api_url() -> str:
    import widget_config

    for position, value in enumerate(sys.argv[:-1]):
        if value == "--api":
            return sys.argv[position + 1].rstrip("/")
    return str(widget_config.load().get("api_url") or "http://127.0.0.1:8000").rstrip("/")


def _healthy(api_url: str) -> bool:
    try:
        return requests.get(f"{api_url}/health", timeout=2).ok
    except requests.RequestException:
        return False


def _ensure_server(api_url: str) -> None:
    if _healthy(api_url):
        return
    parsed = urlparse(api_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return
    executable = Path(sys.executable).with_name("Cerebro.exe")
    if not executable.is_file():
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    command = [str(executable), "--no-browser"]
    if parsed.port:
        command.extend(["--port", str(parsed.port)])
    subprocess.Popen(command, creationflags=flags)
    for _ in range(30):
        if _healthy(api_url):
            return
        time.sleep(0.5)


def _setting(api_url: str, key: str, default=None):
    try:
        payload = requests.get(f"{api_url}/api/system/settings", timeout=4).json()
        field = next(item for item in payload.get("fields", []) if item.get("key") == key)
        return field.get("value", default)
    except (requests.RequestException, StopIteration, ValueError):
        return default


def _run_desktop_helpers(api_url: str) -> None:
    from activity_recorder import ActivityRecorder
    from agent import DesktopAgent

    for target in (DesktopAgent(api_url).run, ActivityRecorder(api_url).run):
        threading.Thread(target=target, daemon=True).start()

    enabled = _setting(api_url, "SCREENPIPE_ENABLED", default=True) in (True, "true", "1", 1)
    if enabled:
        from screenpipe_launcher import launch_if_installed

        screenpipe_url = str(_setting(api_url, "SCREENPIPE_URL", "http://127.0.0.1:3030"))
        launch_if_installed(screenpipe_url)

if __name__ == "__main__":
    import widget

    api_url = _api_url()
    _ensure_server(api_url)
    _run_desktop_helpers(api_url)
    sys.exit(widget.main())
