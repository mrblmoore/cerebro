"""
Copilot Studio bridge — optional, and deliberately additive.

Cerebro remains the whole assistant on its own. This publishes a slice of what
it knows into a folder your Copilot Studio agent can read, and accepts commands
the agent writes back — so asking Copilot on your phone "what am I working on?"
gets a real answer, and "add that to my project log" reaches the file on your
desk.

The folder is the entire integration surface, for the same reason the Outlook
bridge uses one: a cloud agent cannot call into a laptop, and a OneDrive-synced
folder needs no app registration, no token and no inbound firewall rule.

    <bridge>/context.json     Cerebro writes  →  agent reads
    <bridge>/memory.json      Cerebro writes  →  agent reads
    <bridge>/style.json       Cerebro writes  →  agent reads
    <bridge>/commands/*.json  agent writes    →  Cerebro executes
    <bridge>/results/*.json   Cerebro writes  →  agent reads

Two safety rules hold regardless of settings:

* commands may only do **local** things — read context, search, edit a local
  document, create a task. Anything that leaves the machine (an email, a Teams
  message) still goes through Cerebro's own approval path;
* published memory is the distilled, redacted kind. Raw screenshots and captured
  keystrokes never cross the boundary.
"""

import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings

#: A sync can be triggered from three places — the scheduler, the API, the CLI.
#: This serialises the command drain so two overlapping syncs cannot both pick up
#: the same command file and run it twice.
_drain_lock = threading.Lock()

#: Commands the agent is allowed to ask for. Anything else is refused and
#: reported back, so a mistyped or malicious command file cannot reach code that
#: was never meant to be driven remotely.
ALLOWED_COMMANDS = {
    "get_context": "Current case, customer, call/remote state and open document",
    "search_knowledge": "Search the local knowledge base",
    "recall_memory": "Recall what Cerebro remembers about something",
    "list_documents": "Documents recently open on the desktop",
    "read_document": "Read a tracked document's text",
    "append_document": "Add an entry to a local document, under your section",
    "create_task": "Create a scheduled task from an instruction",
    "raise_nudge": "Surface a nudge in the widget",
}

MAX_COMMANDS_PER_SWEEP = 25
RESULT_RETENTION = 200


# --------------------------------------------------------------- locations
def bridge_dir() -> Optional[Path]:
    raw = (settings.COPILOT_BRIDGE_DIR or "").strip()
    return Path(raw).expanduser() if raw else None


def _sub(name: str) -> Optional[Path]:
    base = bridge_dir()
    return (base / name) if base else None


def _write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Write beside the target then rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


# --------------------------------------------------------------- publishing
class CopilotBridge:
    def __init__(self, db: Session):
        self.db = db

    def publish(self) -> Dict[str, Any]:
        """Write the current context, memory and style card for the agent."""
        base = bridge_dir()
        if base is None:
            return {"ok": False, "detail": "No bridge folder configured."}

        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "detail": f"Cannot create {base}: {exc}"}

        written: List[str] = []

        if settings.COPILOT_PUBLISH_CONTEXT:
            _write_atomic(base / "context.json", self._context_payload())
            written.append("context.json")

        if settings.COPILOT_PUBLISH_MEMORY:
            _write_atomic(base / "memory.json", self._memory_payload())
            written.append("memory.json")

        if settings.COPILOT_PUBLISH_STYLE:
            _write_atomic(base / "style.json", self._style_payload())
            written.append("style.json")

        return {"ok": True, "written": written, "folder": str(base)}

    def _context_payload(self) -> Dict[str, Any]:
        from app.models.tracked_document import TrackedDocument
        from app.services.context_engine import ContextEngine
        from app.services.nudge_service import NudgeService

        engine = ContextEngine(self.db)
        context = engine.get_current_context()
        state = context.to_dict() if context else {}

        recent = (self.db.query(TrackedDocument)
                  .order_by(TrackedDocument.last_seen.desc()).limit(5).all())

        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "note": "Live desktop context published by Cerebro. May be up to a "
                    "minute old; treat as 'recently' rather than 'right now'.",
            "current_case": state.get("crm_case"),
            "customer": state.get("customer"),
            "crm_system": state.get("crm_system"),
            "on_a_call": state.get("call_active", False),
            "remote_session": state.get("remote_session_active", False),
            "remote_host": state.get("remote_host"),
            "active_application": state.get("active_application"),
            "window_title": state.get("window_title"),
            "recent_documents": [
                {"name": d.name, "kind": d.kind, "case_id": d.case_id,
                 "path": d.path, "last_seen": d.last_seen.isoformat() if d.last_seen else None}
                for d in recent
            ],
            "suggestions": engine.current_recommendations(),
            "open_nudges": [n.to_dict() for n in NudgeService(self.db).open_nudges(limit=5)],
        }

    def _memory_payload(self) -> Dict[str, Any]:
        """
        Distilled memories only.

        Raw activity — screenshots, captured keystrokes — is deliberately absent.
        The point of the boundary is that only what Cerebro has already reduced
        to a durable, redacted fact ever leaves the machine.
        """
        from app.services.memory_service import MemoryService
        from app.services.redaction import redact

        memories = MemoryService(self.db).list_memories(limit=settings.COPILOT_MEMORY_LIMIT)

        # Redact once more on the way out. Memory content is distilled but not
        # itself guaranteed clean — a resolution note can mention a credential —
        # and this file syncs to the cloud, so it is the last chance to catch it.
        shared = []
        for m in memories:
            clean_content, _ = redact(m.content or "", redact_pii=True)
            clean_title, _ = redact(m.title or "", redact_pii=True)
            shared.append({
                "type": m.memory_type, "title": clean_title, "content": clean_content,
                "case_id": m.case_id, "customer": m.customer,
                "confidence": m.confidence,
            })

        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "note": "Durable facts Cerebro has learned. Use these to answer as the "
                    "user would, and prefer them over general knowledge.",
            "count": len(shared),
            "memories": shared,
        }

    def _style_payload(self) -> Dict[str, Any]:
        from app.services.style_service import StyleService, persona

        style = StyleService(self.db)
        row = style._row()
        profile = {}
        try:
            profile = json.loads(row.profile) if row.profile else {}
        except ValueError:
            profile = {}

        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "note": "How the user writes. Match this voice when drafting on their "
                    "behalf — it is learned from what they actually send.",
            "persona": persona(),
            "style_card": row.style_card,
            "guidance": row.guidance,
            "profile": profile,
            "samples": [s.get("text") for s in style._load(row.samples)[-3:]],
        }

    # ----------------------------------------------------------- commands
    def drain_commands(self) -> Dict[str, Any]:
        """Execute pending command files and write results back."""
        folder = _sub("commands")
        if folder is None or not settings.COPILOT_ACCEPT_COMMANDS:
            return {"ok": True, "executed": 0, "detail": "commands disabled"}
        if not folder.exists():
            return {"ok": True, "executed": 0}

        # Only one drain runs at a time. Overlapping syncs would otherwise both
        # glob the same file before either archives it, and an auto-mode command
        # would run twice — a duplicate task, a duplicate document append.
        if not _drain_lock.acquire(blocking=False):
            return {"ok": True, "executed": 0, "detail": "another sync is running"}

        try:
            executed = failed = 0
            now = datetime.now().timestamp()

            for path in sorted(folder.glob("*.json"))[:MAX_COMMANDS_PER_SWEEP]:
                try:
                    if now - path.stat().st_mtime < 1.0:
                        continue  # still being written
                except OSError:
                    continue

                # Claim the file by renaming it before running it. rename is
                # atomic, so even if the lock were somehow bypassed a file can
                # only ever be claimed — and thus run — once.
                claimed = path.with_suffix(".json.running")
                try:
                    path.rename(claimed)
                except OSError:
                    continue  # already taken

                result = self._run_command_file(claimed)
                if result.get("ok"):
                    executed += 1
                else:
                    failed += 1
                # Report and archive under the command's real name, not the
                # transient ``.running`` one it was claimed as.
                self._write_result(path.name, result)
                self._archive(claimed, original_name=path.name)

            if executed or failed:
                logger.info("copilot", "Executed agent commands",
                            {"executed": executed, "failed": failed})
            return {"ok": True, "executed": executed, "failed": failed}
        finally:
            _drain_lock.release()

    def _run_command_file(self, path: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"Not valid JSON: {exc}"}

        if not isinstance(payload, dict):
            return {"ok": False, "error": "Expected a JSON object."}

        action = str(payload.get("action") or "").strip()
        if action not in ALLOWED_COMMANDS:
            return {"ok": False, "error": f"Unknown or not-permitted action {action!r}. "
                                          f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"}

        # A command that changes something waits for approval unless the user
        # turned that off — the agent is a guest on this machine.
        changes_things = action in ("append_document", "create_task")
        if changes_things and settings.COPILOT_COMMAND_MODE == "approve":
            return self._stage_for_approval(action, payload)

        try:
            return self._dispatch(action, payload)
        except Exception as exc:
            logger.error("copilot", "Command failed", {"action": action, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _stage_for_approval(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.nudge_service import NudgeService

        summary = payload.get("summary") or f"Copilot asked to {action.replace('_', ' ')}"
        nudge = NudgeService(self.db).raise_nudge(
            title="Copilot request",
            body=f"{summary}. Approve it?",
            kind="copilot_request", priority="medium",
            dedupe_key=f"copilot:{action}:{json.dumps(payload, sort_keys=True)[:120]}",
            action={"type": "copilot_command", "command": payload})

        # raise_nudge returns None when an identical request was already raised.
        # Reporting "staged" then would be a lie — the request would vanish with
        # no widget entry — so tell the agent it is already pending instead.
        if nudge is None:
            return {"ok": True, "staged": True, "duplicate": True,
                    "message": "This request is already waiting for the user's "
                               "approval in Cerebro — no need to send it again."}

        return {"ok": True, "staged": True,
                "message": "Staged for the user to approve in Cerebro. "
                           "Tell them it is waiting in the widget."}

    def _dispatch(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"_cmd_{action}")
        return handler(payload)

    # ------------------------------------------------------- command impls
    def _cmd_get_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "context": self._context_payload()}

    def _cmd_search_knowledge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.rag_service import RAGService

        query = str(payload.get("query") or "").strip()
        if len(query) < 2:
            return {"ok": False, "error": "A query is required."}
        results = RAGService(self.db).search(query, limit=int(payload.get("limit", 5)))
        return {"ok": True, "query": query, "results": results}

    def _cmd_recall_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.memory_service import MemoryService

        query = str(payload.get("query") or "").strip()
        if len(query) < 2:
            return {"ok": False, "error": "A query is required."}
        return {"ok": True, "memories": MemoryService(self.db).recall(
            query, limit=int(payload.get("limit", 6)),
            case_id=payload.get("case_id"), customer=payload.get("customer"))}

    def _cmd_list_documents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.document_service import DocumentService

        documents = DocumentService(self.db).recent(limit=int(payload.get("limit", 10)))
        return {"ok": True, "documents": [d.to_dict() for d in documents]}

    def _cmd_read_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.models.tracked_document import TrackedDocument
        from app.services.document_service import DocumentService

        name = str(payload.get("name") or payload.get("path") or "").strip()
        if not name:
            return {"ok": False, "error": "A document name or path is required."}

        record = (self.db.query(TrackedDocument)
                  .filter(TrackedDocument.name == name).first()
                  or self.db.query(TrackedDocument)
                  .filter(TrackedDocument.path.like(f"%{name}%")).first())
        if record is None:
            return {"ok": False, "error": f"No tracked document matching {name!r}."}

        content = DocumentService(self.db).content(record, full=False)
        return {"ok": True, "name": record.name, "kind": record.kind,
                "outline": content.get("outline"), "text": content.get("text")}

    def _cmd_append_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.models.task import Task
        from app.services.task_executors import execute

        name = str(payload.get("name") or payload.get("path") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not name or not text:
            return {"ok": False, "error": "Both a document and the text to add are required."}

        task = Task(
            title=f"Copilot: add to {name}", kind="document_update",
            instruction=text, autonomous=True, attribution="user",
            status="active", source="copilot",
            spec=json.dumps({"document": name, "section": payload.get("section", "mine"),
                             "content_hint": text}))
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        result = execute(self.db, task)
        return {"ok": bool(result.get("ok")), "detail": result.get("detail"),
                "summary": result.get("summary")}

    def _cmd_create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.task_service import TaskService

        instruction = str(payload.get("instruction") or "").strip()
        if len(instruction) < 3:
            return {"ok": False, "error": "An instruction is required."}
        task = TaskService(self.db).create_from_instruction(instruction, source="copilot")
        return {"ok": True, "task": task.to_dict()}

    def _cmd_raise_nudge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.nudge_service import NudgeService

        title = str(payload.get("title") or "From Copilot").strip()
        body = str(payload.get("body") or "").strip()
        if not body:
            return {"ok": False, "error": "A body is required."}
        NudgeService(self.db).raise_nudge(title=title, body=body, kind="copilot",
                                          priority=payload.get("priority", "medium"))
        return {"ok": True}

    # --------------------------------------------------------- approvals
    def run_approved_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Carry out a command the user approved from a nudge.

        The command was validated when it was staged, so this dispatches it
        directly — the approval is what the ``approve`` mode was waiting for, so
        it must not be re-queued for approval a second time.
        """
        action = str(payload.get("action") or "").strip()
        if action not in ALLOWED_COMMANDS:
            return {"ok": False, "error": f"Not a permitted action: {action!r}"}
        try:
            return self._dispatch(action, payload)
        except Exception as exc:
            logger.error("copilot", "Approved command failed",
                         {"action": action, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------- files
    def _write_result(self, command_name: str, result: Dict[str, Any]) -> None:
        folder = _sub("results")
        if folder is None:
            return
        payload = {
            "command_file": command_name,
            "completed_at": datetime.utcnow().isoformat() + "Z",
            **result,
        }
        stem = Path(command_name).stem
        _write_atomic(folder / f"result-{stem}.json", payload)
        self._trim_results(folder)

    @staticmethod
    def _trim_results(folder: Path) -> None:
        """Keep the results folder from growing without bound."""
        try:
            files = sorted(folder.glob("result-*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in files[RESULT_RETENTION:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _archive(path: Path, original_name: str = None) -> None:
        archive = _sub("commands/processed")
        # The file on disk is the claimed ``.running`` copy; archive it under the
        # command's real name so the processed folder reads cleanly.
        name = original_name or path.name
        try:
            if archive is None:
                path.unlink(missing_ok=True)
                return
            archive.mkdir(parents=True, exist_ok=True)
            target = archive / name
            if target.exists():
                stem = Path(name).stem
                suffix = Path(name).suffix
                target = archive / f"{stem}-{datetime.utcnow():%H%M%S%f}{suffix}"
            shutil.move(str(path), str(target))
        except OSError:
            pass

    # ------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        if not settings.COPILOT_BRIDGE_ENABLED:
            return {"ok": True, "enabled": False,
                    "detail": "Copilot Studio integration is off"}

        base = bridge_dir()
        if base is None:
            return {"ok": False, "enabled": True,
                    "detail": "Turned on, but no bridge folder is set"}
        if not base.exists():
            return {"ok": False, "enabled": True, "folder": str(base),
                    "detail": f"Folder does not exist yet: {base}"}

        pending = len(list((base / "commands").glob("*.json"))) \
            if (base / "commands").exists() else 0
        published = [f.name for f in base.glob("*.json")]

        return {
            "ok": True, "enabled": True, "folder": str(base),
            "published": published, "pending_commands": pending,
            "accepting_commands": settings.COPILOT_ACCEPT_COMMANDS,
            "command_mode": settings.COPILOT_COMMAND_MODE,
            "detail": (f"Sharing {len(published)} file(s) with Copilot · "
                       f"{pending} command(s) waiting"),
        }


def sync(db: Session) -> Dict[str, Any]:
    """One full cycle — called by the scheduler and by the Sync now button."""
    if not settings.COPILOT_BRIDGE_ENABLED:
        return {"ok": True, "skipped": True}
    bridge = CopilotBridge(db)
    published = bridge.publish()
    commands = bridge.drain_commands()
    return {"ok": published.get("ok", False), "published": published,
            "commands": commands}
