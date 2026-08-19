"""Detect and start an official Screenpipe installation without bundling it."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests


def is_running(base_url: str = "http://127.0.0.1:3030") -> bool:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/health", timeout=2)
        return response.ok
    except requests.RequestException:
        return False


def find_executable() -> Optional[Path]:
    """Find a user-installed Screenpipe app in PATH or common Windows locations."""
    from_path = shutil.which("screenpipe")
    if from_path:
        return Path(from_path)

    if sys.platform != "win32":
        return None

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", ""))
    candidates = [
        local / "Programs" / "screenpipe" / "screenpipe.exe",
        local / "Programs" / "Screenpipe" / "screenpipe.exe",
        local / "screenpipe" / "screenpipe.exe",
        program_files / "screenpipe" / "screenpipe.exe",
        program_files / "Screenpipe" / "screenpipe.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def launch_if_installed(base_url: str = "http://127.0.0.1:3030") -> bool:
    """Start Screenpipe when installed and not already serving its API."""
    if is_running(base_url):
        return True
    executable = find_executable()
    if executable is None:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([str(executable)], creationflags=flags)
        return True
    except OSError:
        return False
