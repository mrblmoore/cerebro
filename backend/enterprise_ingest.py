#!/usr/bin/env python3
"""
Cerebro enterprise importer — Outlook and Teams via Power Automate.

Power Automate writes one JSON file per Outlook email or Teams message into a
folder (usually OneDrive-synced). This reads that folder into Cerebro.

    python backend/enterprise_ingest.py "C:\\Users\\you\\OneDrive\\Cerebro\\enterprise-inbox" --watch

With no folder argument it uses ENTERPRISE_INBOX_DIR from your settings.

Note that Cerebro's backend already sweeps the inbox itself whenever the bridge
is enabled in Settings → Enterprise Bridge, so you usually do not need to run
this at all. It is here for one-off imports, for replaying an archive, and for
pushing into a Cerebro running on another machine with ``--api``.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _local_ingest(folder: Path, once: bool, interval: float) -> int:
    from app.core import settings_store  # noqa: F401  (loads .env)
    from app.core.config import settings
    from app.core.database import SessionLocal, init_db
    from app.services import enterprise_service

    if folder:
        # Point this run at the given folder without writing it to .env.
        object.__setattr__(settings, "ENTERPRISE_INBOX_DIR", str(folder))
    object.__setattr__(settings, "ENTERPRISE_ENABLED", True)

    inbox = enterprise_service.inbox_dir()
    if inbox is None:
        print("No inbox folder given and none configured.\n"
              "Pass a folder, or set it in Settings → Enterprise Bridge.")
        return 1
    if not inbox.exists():
        print(f"Folder does not exist: {inbox}")
        return 1

    init_db()
    print(f"Cerebro enterprise importer → {inbox}")

    total = 0
    while True:
        db = SessionLocal()
        try:
            result = enterprise_service.drain_inbox(db)
        finally:
            db.close()

        if result.get("ingested") or result.get("failed"):
            total += result.get("ingested", 0)
            print(f"  ingested {result['ingested']}  "
                  f"duplicates {result.get('duplicates', 0)}  "
                  f"failed {result.get('failed', 0)}  (total {total})")
            for detail in result.get("details", []):
                if detail.get("status") in ("invalid", "error"):
                    print(f"    ! {detail.get('file')}: {detail.get('detail')}")

        if once:
            print(f"Done. {total} message(s) ingested.")
            return 0

        time.sleep(interval)


def _api_ingest(folder: Path, api: str, once: bool, interval: float) -> int:
    """Push files to a Cerebro running elsewhere, over HTTP."""
    import requests

    if not folder or not folder.exists():
        print(f"Folder does not exist: {folder}")
        return 1

    api = api.rstrip("/")
    print(f"Cerebro enterprise importer → {folder} → {api}")
    archive = folder / "processed"
    total = 0

    while True:
        for path in sorted(folder.glob("*.json")):
            try:
                if time.time() - path.stat().st_mtime < 1.0:
                    continue  # still being written
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                print(f"  ! {path.name}: {exc}")
                continue

            for item in (payload if isinstance(payload, list) else [payload]):
                try:
                    response = requests.post(f"{api}/api/enterprise/ingest",
                                             json=item, timeout=15)
                    response.raise_for_status()
                    if response.json().get("status") == "ingested":
                        total += 1
                except requests.RequestException as exc:
                    print(f"  ! could not send {path.name}: {exc}")
                    break
            else:
                archive.mkdir(parents=True, exist_ok=True)
                path.replace(archive / path.name)
                print(f"  ingested {path.name}  (total {total})")

        if once:
            print(f"Done. {total} message(s) ingested.")
            return 0
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Outlook/Teams JSON files written by Power Automate.")
    parser.add_argument("folder", nargs="?", help="folder to read (default: from settings)")
    parser.add_argument("--watch", action="store_true",
                        help="keep running and ingest new files as they appear")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between sweeps in watch mode")
    parser.add_argument("--api", help="send to a Cerebro at this URL instead of "
                                      "writing to the local database")
    arguments = parser.parse_args()

    folder = Path(arguments.folder).expanduser() if arguments.folder else None

    try:
        if arguments.api:
            return _api_ingest(folder, arguments.api, not arguments.watch,
                               arguments.interval)
        return _local_ingest(folder, not arguments.watch, arguments.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
