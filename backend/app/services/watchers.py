"""
Background folder watching, run inside the backend process.

The documented integration is a standalone importer (``enterprise_ingest.py``),
which is still there and still the right tool for a remote Cerebro or a debug
run. But asking someone to keep a second console window open forever is a poor
default, so when the bridge is enabled the backend sweeps the folder itself.

Polling rather than filesystem events on purpose: the inbox is usually a
OneDrive-synced folder, where sync writes do not always raise the events an
inotify/ReadDirectoryChanges watcher expects.
"""

import threading
from typing import Optional

from app.core import logger
from app.core.config import settings
from app.core.database import SessionLocal

_thread: Optional[threading.Thread] = None
_scheduler_thread: Optional[threading.Thread] = None
_stop = threading.Event()

#: How often the scheduler checks for due tasks (seconds). Nudge scans and
#: retention run less often, counted in ticks.
_last_nudge_scan = 0.0
_last_retention = 0.0


def _sweep_once() -> None:
    from app.services import enterprise_service

    db = SessionLocal()
    try:
        result = enterprise_service.drain_inbox(db)
        if result.get("ingested"):
            logger.info("watcher", "Ingested enterprise messages", {
                "ingested": result["ingested"],
                "duplicates": result.get("duplicates", 0),
            })
        elif not result.get("ok"):
            # Log once per sweep at debug level: a missing folder is a common
            # transient state while OneDrive is still setting itself up.
            logger.debug("watcher", "Inbox unavailable", {"detail": result.get("detail")})
    except Exception as exc:  # a watcher must never take the server down
        logger.error("watcher", "Inbox sweep failed", {"error": str(exc)})
    finally:
        db.close()


def _loop() -> None:
    interval = max(2, int(settings.ENTERPRISE_POLL_SECONDS))
    logger.info("watcher", "Enterprise inbox watcher started", {"interval_s": interval})

    while not _stop.is_set():
        if settings.ENTERPRISE_ENABLED:
            _sweep_once()
        # Re-read the interval each pass so a settings change takes effect
        # without a restart.
        _stop.wait(max(2, int(settings.ENTERPRISE_POLL_SECONDS)))

    logger.info("watcher", "Enterprise inbox watcher stopped")


def _scheduler_loop() -> None:
    """Drive tasks, nudges and retention on a timer."""
    import time

    from app.services import nudge_service, task_service
    from app.services.activity_service import apply_retention

    logger.info("scheduler", "Task scheduler started")
    global _last_nudge_scan, _last_retention

    while not _stop.is_set():
        now = time.time()
        db = SessionLocal()
        try:
            if settings.TASKS_ENABLED:
                result = task_service.run_due_tasks(db)
                if result.get("ran"):
                    logger.info("scheduler", "Ran due tasks", {"count": result["ran"]})

            # Scan for nudges every ~2 minutes.
            if settings.NUDGES_ENABLED and now - _last_nudge_scan > 120:
                nudge_service.scan_for_nudges(db)
                _last_nudge_scan = now

            # Retention sweep every ~6 hours.
            if now - _last_retention > 6 * 3600:
                apply_retention(db)
                _last_retention = now
        except Exception as exc:  # a scheduler must never crash the server
            logger.error("scheduler", "Scheduler tick failed", {"error": str(exc)})
        finally:
            db.close()

        _stop.wait(max(10, int(settings.TASK_TICK_SECONDS)))

    logger.info("scheduler", "Task scheduler stopped")


def start() -> None:
    """
    Start the background threads.

    Started unconditionally, even with features switched off: each loop checks
    its setting on every pass, so enabling a feature in Settings takes effect
    within one interval rather than needing a restart.
    """
    global _thread, _scheduler_thread

    _stop.clear()

    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="cerebro-inbox-watcher",
                                   daemon=True)
        _thread.start()

    if _scheduler_thread is None or not _scheduler_thread.is_alive():
        _scheduler_thread = threading.Thread(target=_scheduler_loop,
                                             name="cerebro-scheduler", daemon=True)
        _scheduler_thread.start()


def stop() -> None:
    _stop.set()


def running() -> bool:
    return _thread is not None and _thread.is_alive()
