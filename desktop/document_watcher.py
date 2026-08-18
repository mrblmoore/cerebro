#!/usr/bin/env python3
"""
Cerebro document watcher — notices the documents you are working on.

Two signals, both cheap and neither requiring any Office add-in:

* **Office lock files.** Word and Excel create ``~$name.docx`` beside a file for
  as long as it is open. That is an exact "this document is open right now"
  signal, and it works no matter how the file was opened.
* **The foreground window title.** On Windows, Word and Excel put the document
  name in the title bar, which catches files outside the watched folders.

Everything found is reported to Cerebro, which reads it and can then answer
questions about it or edit it.

    python desktop/document_watcher.py
    python desktop/document_watcher.py --folder "C:\\Users\\you\\OneDrive - Contoso"
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests

DEFAULT_API = os.environ.get("CEREBRO_API_URL", "http://127.0.0.1:8000")
POLL_SECONDS = 4.0
IS_WINDOWS = sys.platform == "win32"

SUPPORTED = {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pdf", ".csv", ".txt", ".md"}

#: Office's own scratch files, Cerebro's backups, and anything hidden.
IGNORED_PREFIXES = ("~$", ".~", "._")
IGNORED_MARKERS = (".cerebro-backup-",)

#: "Case notes.docx - Word", "tickets.xlsx - Excel", "Report - PowerPoint"
TITLE_PATTERN = re.compile(
    r"^(?P<name>.+?\.(?:docx?|xlsx?|pptx?|pdf))\s*[-–—]\s*(?:Word|Excel|PowerPoint|Adobe)",
    re.IGNORECASE,
)


def interesting(path: Path) -> bool:
    name = path.name
    if name.startswith(IGNORED_PREFIXES) or any(m in name for m in IGNORED_MARKERS):
        return False
    return path.suffix.lower() in SUPPORTED


def default_folders() -> List[Path]:
    """Folders worth watching when the user has not named any."""
    home = Path.home()
    candidates = [home / "Documents", home / "Desktop", home / "Downloads"]

    # OneDrive and synced SharePoint libraries sit directly under the profile
    # with the tenant name appended, e.g. "OneDrive - Contoso Ltd".
    try:
        for entry in home.iterdir():
            if entry.is_dir() and entry.name.lower().startswith("onedrive"):
                candidates.append(entry)
    except OSError:
        pass

    return [folder for folder in candidates if folder.exists()]


# ------------------------------------------------------------------ signals
def open_documents(folders: Iterable[Path], max_depth: int = 4) -> Set[Path]:
    """
    Documents Office currently has open, found by their lock files.

    Bounded depth because a synced OneDrive tree can be enormous and this runs
    every few seconds.
    """
    found: Set[Path] = set()

    for folder in folders:
        base_depth = len(folder.parts)
        try:
            for root, directories, files in os.walk(folder):
                if len(Path(root).parts) - base_depth >= max_depth:
                    directories[:] = []
                    continue
                directories[:] = [d for d in directories
                                  if not d.startswith(".") and d.lower() != "node_modules"]

                for name in files:
                    if not name.startswith("~$"):
                        continue
                    # Office truncates the leading characters for long names, so
                    # match on the suffix rather than trusting the whole stem.
                    stem = name[2:]
                    for candidate in Path(root).glob(f"*{stem[-20:]}"):
                        if candidate.is_file() and interesting(candidate):
                            found.add(candidate.resolve())
        except OSError:
            continue

    return found


def foreground_document(folders: Iterable[Path]) -> Optional[Path]:
    """The document named in the active window's title, if we can find it."""
    if not IS_WINDOWS:
        return None

    import ctypes

    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None

    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)

    match = TITLE_PATTERN.match(buffer.value.strip())
    if not match:
        return None

    filename = match.group("name").strip()
    for folder in folders:
        try:
            for candidate in folder.rglob(filename):
                if candidate.is_file():
                    return candidate.resolve()
        except OSError:
            continue
    return None


def recently_modified(folders: Iterable[Path], within_seconds: int = 300,
                      limit: int = 10) -> List[Path]:
    """
    Recently changed documents.

    The fallback signal on macOS and Linux, where there is no lock file to read
    and no foreground-window API here — "you saved this two minutes ago" is a
    reasonable stand-in for "you are working on this".
    """
    cutoff = time.time() - within_seconds
    found: List[Path] = []

    for folder in folders:
        if len(found) >= limit:
            break   # the limit is a total, not a per-folder allowance
        try:
            for path in folder.rglob("*"):
                if len(found) >= limit:
                    break
                if not path.is_file() or not interesting(path):
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        found.append(path.resolve())
                except OSError:
                    continue
        except OSError:
            continue

    return found


# ------------------------------------------------------------------ runner
class DocumentWatcher:
    def __init__(self, api_url: str, folders: List[Path], interval: float):
        self.api_url = api_url.rstrip("/")
        self.folders = folders
        self.interval = interval
        self.session = requests.Session()
        #: path -> mtime last reported, so an unchanged file is reported once.
        self.reported: Dict[str, float] = {}

    def report(self, path: Path) -> None:
        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if self.reported.get(key) == mtime:
            return

        try:
            response = self.session.post(
                f"{self.api_url}/api/documents/observe",
                json={"path": key, "discovered_by": "desktop_watcher"},
                timeout=30,
            )
            if response.status_code == 200:
                self.reported[key] = mtime
                data = response.json()
                note = f" · case {data['case_id']}" if data.get("case_id") else ""
                print(f"  → {path.name} ({data.get('kind')}){note}")
            elif response.status_code == 409:
                print("  ! document reading is turned off in Cerebro's settings")
            elif response.status_code not in (404, 415):
                print(f"  ! {path.name}: {response.status_code} {response.text[:120]}")
        except requests.RequestException as exc:
            print(f"  ! could not reach Cerebro: {exc}")

    def wait_for_api(self) -> None:
        announced = False
        while True:
            try:
                self.session.get(f"{self.api_url}/health", timeout=4).raise_for_status()
                if announced:
                    print("Connected.")
                return
            except requests.RequestException:
                if not announced:
                    print(f"Waiting for Cerebro at {self.api_url} — "
                          "start it with 'python cerebro.py start'.")
                    announced = True
                time.sleep(3)

    def tick(self) -> None:
        seen: Set[Path] = set(open_documents(self.folders))

        active = foreground_document(self.folders)
        if active:
            seen.add(active)

        if not seen and not IS_WINDOWS:
            seen.update(recently_modified(self.folders))

        for path in seen:
            self.report(path)

    def run(self) -> int:
        print(f"Cerebro document watcher → {self.api_url}")
        for folder in self.folders:
            print(f"  watching {folder}")
        if not self.folders:
            print("  no folders to watch — pass --folder or set watched folders "
                  "in Settings → Documents")
            return 1

        self.wait_for_api()
        print("\nWatching for open documents. Press Ctrl+C to stop.\n")

        try:
            while True:
                self.tick()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\nWatcher stopped.")
        return 0


def configured_folders(api_url: str) -> List[Path]:
    """Ask Cerebro which folders it was told to watch."""
    try:
        response = requests.get(f"{api_url.rstrip('/')}/api/system/settings", timeout=5)
        response.raise_for_status()
        fields = {f["key"]: f.get("value") for f in response.json().get("fields", [])}
    except (requests.RequestException, ValueError, KeyError):
        return []

    folders: List[Path] = []
    for key in ("DOCUMENT_WATCH_DIRS", "SHAREPOINT_SYNC_ROOTS"):
        for part in re.split(r"[;\n]", str(fields.get(key) or "")):
            part = part.strip()
            if part and Path(part).expanduser().exists():
                folders.append(Path(part).expanduser())
    return folders


def main() -> int:
    parser = argparse.ArgumentParser(description="Cerebro document watcher")
    parser.add_argument("--api", default=DEFAULT_API, help="Cerebro API base URL")
    parser.add_argument("--folder", action="append", dest="folders",
                        help="folder to watch (repeatable)")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS,
                        help="seconds between sweeps")
    arguments = parser.parse_args()

    if arguments.folders:
        folders = [Path(f).expanduser() for f in arguments.folders]
    else:
        folders = configured_folders(arguments.api) or default_folders()

    folders = [f for f in folders if f.exists()]
    return DocumentWatcher(arguments.api, folders, arguments.interval).run()


if __name__ == "__main__":
    sys.exit(main())
