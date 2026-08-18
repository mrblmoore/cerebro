#!/usr/bin/env python3
"""
Entry point for the packaged Cerebro executable.

The installed build has no Python, no virtual environment and no source tree —
just ``Cerebro.exe``. This starts the API, opens the browser at the right page,
and keeps running until the window is closed.

    Cerebro.exe              start and open the dashboard
    Cerebro.exe --no-browser start only
    Cerebro.exe --port 8010  use a different port
"""

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# The startup banner and log lines contain box-drawing characters and may carry
# non-ASCII data. A Windows console defaults to cp1252; force UTF-8 so the exe's
# output renders cleanly instead of garbling or raising. Guarded because a
# windowed build can have no console at all.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# In a source checkout the backend is a sibling directory; in the frozen build
# it is bundled at the top level of the archive.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent / "backend", _HERE):
    if (candidate / "app").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _wait_and_open(url: str, path: str = "/") -> None:
    """Open a browser once the server answers, without blocking startup."""
    import urllib.error
    import urllib.request

    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    else:
        return

    try:
        webbrowser.open(f"{url}{path}")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="Cerebro", description="Cerebro — local support copilot")
    parser.add_argument("--host", help="address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--widget", action="store_true", help="also launch the desktop widget")
    arguments = parser.parse_args()

    if arguments.host:
        os.environ["HOST"] = arguments.host
    if arguments.port:
        os.environ["PORT"] = str(arguments.port)

    import uvicorn

    from app.core.config import settings
    from app.core.paths import DATA_DIR

    host = "localhost" if settings.HOST in ("0.0.0.0", "::") else settings.HOST
    url = f"http://{host}:{settings.PORT}"

    print(f"  Cerebro is starting at {url}")
    print(f"  Your data lives in {DATA_DIR}")
    print("  Close this window to stop Cerebro.\n")

    if not arguments.no_browser:
        target = "/" if settings.SETUP_COMPLETED else "/setup"
        threading.Thread(target=_wait_and_open, args=(url, target), daemon=True).start()

    if arguments.widget:
        threading.Thread(target=_launch_widget, args=(url,), daemon=True).start()

    try:
        # Import the app object directly: the frozen build has no module search
        # path for uvicorn's "app.main:app" string form to resolve against.
        from app.main import app

        uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info")
    except KeyboardInterrupt:
        pass
    return 0


def _launch_widget(url: str) -> None:
    """Start the widget executable that ships alongside this one."""
    import subprocess

    widget = Path(sys.executable).with_name("CerebroWidget.exe")
    if not widget.exists():
        return
    time.sleep(2)
    try:
        subprocess.Popen([str(widget), "--api", url])
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
