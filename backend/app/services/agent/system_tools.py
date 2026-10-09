"""
General-purpose Ask tools: files and folders, web pages, and (optionally) commands.

Reads run immediately. Anything that changes the computer — writing a file,
running a command — is proposed as an approval card first, like every other
change Ask makes. File writes keep a backup beside the file and can be undone.
"""

import fnmatch
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import settings
from app.services.agent import actions
from app.services.agent.analysis_tools import summarise_log
from app.services.agent.registry import ToolContext, schema, string_param, tool

MAX_READ_CHARS = 30_000
MAX_LIST = 200
MAX_FETCH_BYTES = 2_000_000
COMMAND_TIMEOUT_SECONDS = 120
LOG_SUFFIXES = (".log", ".txt", ".out", ".err", ".trace", ".evtx.txt")
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "$recycle.bin", "system volume information"}


def _path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser((value or "").strip().strip('"'))))


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def _stamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "?"


def read_any(path: Path, limit: int = MAX_READ_CHARS) -> str:
    """Text of a file: Office/PDF/CSV through the document readers, anything else as text."""
    from app.services import document_readers

    if document_readers.is_supported(path) and path.suffix.lower() not in (".txt", ".log", ".md"):
        data = document_readers.read(path)
        text = data.get("text") or data.get("content") or ""
        if not text and data.get("paragraphs"):
            text = "\n".join(str(p) for p in data["paragraphs"])
        if not text:
            text = str(data)[:limit]
    else:
        text = path.read_bytes()[: limit * 4].decode("utf-8", errors="replace")
    return text[:limit]


# ================================================================ reading
@tool("list_folder",
      "List the files and sub-folders in a folder on this computer (name, size, modified time). "
      "Use pattern (e.g. *.log) to narrow it and recursive to include sub-folders.",
      schema(["path"], path=string_param("Folder path."),
             pattern=string_param("Optional file-name pattern such as *.log or report*.xlsx."),
             recursive=string_param("'true' to include sub-folders.")),
      label="List a folder", activity="searching")
def list_folder(ctx: ToolContext, path: str = "", pattern: str = "", recursive: str = "", **_) -> dict:
    root = _path(path)
    if not root.is_dir():
        return {"content": f"{root} is not a folder I can open.", "summary": "Not found"}
    deep = str(recursive).lower() in ("true", "1", "yes")
    rows: List[tuple] = []
    try:
        walker = os.walk(root) if deep else [(str(root), [d.name for d in root.iterdir() if d.is_dir()],
                                              [f.name for f in root.iterdir() if f.is_file()])]
        for current, dirs, files in walker:
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
            for name in files:
                if pattern and not fnmatch.fnmatch(name.lower(), pattern.lower()):
                    continue
                full = Path(current) / name
                try:
                    rows.append((full, full.stat().st_mtime, full.stat().st_size))
                except OSError:
                    continue
            if len(rows) > 5000:
                break
    except OSError as exc:
        return {"content": f"Couldn't read {root}: {exc}", "summary": "Failed"}
    rows.sort(key=lambda r: -r[1])
    lines = [f"{p.relative_to(root)} · {_human(s)} · {_stamp(p)}" for p, _m, s in rows[:MAX_LIST]]
    more = f"\n…and {len(rows) - MAX_LIST} more (newest shown first)." if len(rows) > MAX_LIST else ""
    return {"content": (f"{len(rows)} file(s) in {root}:\n" + "\n".join(lines) + more) if rows
            else f"No files in {root}" + (f" matching {pattern}" if pattern else "") + ".",
            "summary": f"{len(rows)} file(s)"}


@tool("find_in_files",
      "Search inside the text of files in a folder for a word, phrase or regular expression. "
      "Returns matching lines with file and line number.",
      schema(["path", "text"], path=string_param("Folder (or file) to search."),
             text=string_param("Text or regex to look for."),
             pattern=string_param("Optional file-name pattern, e.g. *.log.")),
      label="Search in files", activity="searching")
def find_in_files(ctx: ToolContext, path: str = "", text: str = "", pattern: str = "", **_) -> dict:
    root = _path(path)
    if not text:
        return {"content": "Say what to look for.", "summary": "Needs text"}
    try:
        rx = re.compile(text, re.I)
    except re.error:
        rx = re.compile(re.escape(text), re.I)
    files = [root] if root.is_file() else [
        Path(c) / n for c, d, fs in os.walk(root) for n in fs
        if not any(part.lower() in _SKIP_DIRS for part in Path(c).parts)
        and (fnmatch.fnmatch(n.lower(), pattern.lower()) if pattern else True)]
    hits: List[str] = []
    for file in files[:2000]:
        try:
            if file.stat().st_size > 50_000_000:
                continue
            with open(file, "r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if rx.search(line):
                        hits.append(f"{file}:{number}: {line.strip()[:220]}")
                        if len(hits) >= 60:
                            break
        except OSError:
            continue
        if len(hits) >= 60:
            break
    return {"content": "\n".join(hits) if hits else f"No matches for “{text}”.",
            "summary": f"{len(hits)} match(es)"}


@tool("read_file",
      "Read any file on this computer by its path — text, logs, CSV, Word, Excel, PowerPoint or PDF. "
      "Long files are shortened; use find_in_files or review_log for big logs.",
      schema(["path"], path=string_param("Full path of the file.")),
      label="Read a file", activity="searching")
def read_file(ctx: ToolContext, path: str = "", **_) -> dict:
    target = _path(path)
    if not target.is_file():
        return {"content": f"No file at {target}.", "summary": "Not found"}
    try:
        text = read_any(target)
    except Exception as exc:  # noqa: BLE001 - explained to the model
        return {"content": f"Couldn't read {target.name}: {exc}", "summary": "Read failed"}
    ref = ctx.cite({"title": target.name, "kind": "file", "locator": str(target),
                    "excerpt": text[:800]}, "F")
    return {"content": f"[{ref}] {target} ({_human(target.stat().st_size)}, modified {_stamp(target)}):\n{text}",
            "summary": target.name}


@tool("review_logs_in_folder",
      "Analyse every log in a folder: groups repeated errors and warnings by file, with counts "
      "and first/last times. Use for 'look at this folder and tell me what's failing'.",
      schema(["path"], path=string_param("Folder holding the logs."),
             focus=string_param("Optional word, error code or ID to concentrate on."),
             days=string_param("Only logs modified in the last N days (default 7).")),
      label="Review a folder of logs", activity="searching")
def review_logs_in_folder(ctx: ToolContext, path: str = "", focus: str = "", days: str = "7", **_) -> dict:
    root = _path(path)
    if not root.is_dir():
        return {"content": f"{root} is not a folder.", "summary": "Not found"}
    try:
        cutoff = datetime.now().timestamp() - float(days or 7) * 86400
    except ValueError:
        cutoff = datetime.now().timestamp() - 7 * 86400
    files = [Path(c) / n for c, _d, fs in os.walk(root) for n in fs
             if n.lower().endswith(LOG_SUFFIXES) and (Path(c) / n).stat().st_mtime >= cutoff]
    files.sort(key=lambda p: -p.stat().st_mtime)
    if not files:
        return {"content": f"No log files changed in the last {days} day(s) in {root}.",
                "summary": "No logs"}
    parts = []
    for file in files[:12]:
        size = file.stat().st_size
        with open(file, "rb") as handle:
            if size > 5_000_000:
                handle.seek(size - 5_000_000)
            text = handle.read().decode("utf-8", errors="replace")
        parts.append(f"## {file.name} ({_human(size)}, modified {_stamp(file)})\n"
                     + summarise_log(text, focus, limit=5))
    skipped = f"\n({len(files) - 12} older log file(s) not shown.)" if len(files) > 12 else ""
    return {"content": "\n\n".join(parts) + skipped, "summary": f"{min(len(files), 12)} log(s)"}


@tool("fetch_url",
      "Download a web page or text/JSON file from an http(s) link and return its readable text. "
      "Use for public pages or links the user gives. Pages that need a sign-in are not reachable "
      "this way (SharePoint and the connected systems have their own tools).",
      schema(["url"], url=string_param("The http(s) link.")),
      label="Fetch a web page", activity="browsing")
def fetch_url(ctx: ToolContext, url: str = "", **_) -> dict:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"content": "Only http(s) links can be fetched.", "summary": "Bad link"}
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Cerebro"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_FETCH_BYTES)
            kind = response.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001 - explained to the model
        return {"content": f"Couldn't fetch {url}: {exc}", "summary": "Failed"}
    text = raw.decode("utf-8", errors="replace")
    if "html" in kind.lower() or text.lstrip().lower().startswith(("<!doctype", "<html")):
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
        text = unescape(re.sub(r"<[^>]+>", " ", text))
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    text = text[:MAX_READ_CHARS]
    ref = ctx.cite({"title": url, "kind": "web", "uri": url, "excerpt": text[:800]}, "W")
    return {"content": f"[{ref}] {url}\n{text}", "summary": url[:60]}


# ================================================================ changes
@tool("write_file",
      "Create or overwrite a text file on this computer (notes, reports, CSV, scripts). "
      "The user approves it first; an existing file is backed up and the change can be undone. "
      "Use mode 'append' to add to the end instead of replacing.",
      schema(["path", "content"], path=string_param("Full path of the file."),
             content=string_param("The text to write."),
             mode=string_param("'replace' (default) or 'append'.")),
      mode="approval", label="Prepare a file change", activity="writing")
def write_file(ctx: ToolContext, path: str = "", content: str = "", mode: str = "replace", **_) -> dict:
    target = _path(path)
    if not path.strip() or target.is_dir():
        return {"content": "Give the full path of a file.", "summary": "Needs a path"}
    before = ""
    if target.is_file():
        try:
            before = read_any(target, 20_000)
        except Exception:  # noqa: BLE001 - binary or unreadable: shown as a change only
            before = "(existing file can't be shown)"
    after = (before + content) if mode == "append" and target.is_file() else content
    action = actions.propose(
        ctx.db, "write_file", "files", f"{'Update' if target.is_file() else 'Create'} {target.name}",
        {"path": str(target), "content": content, "mode": "append" if mode == "append" else "replace"},
        preview={"fields": [{"name": str(target), "before": before[:4000], "after": after[:4000]}],
                 "target": str(target)},
        summary=f"{len(content)} characters")
    ctx.drafts.append(actions.card(action))
    return {"content": f"File change prepared as change #{action.id}; waiting for approval.",
            "summary": "Waiting for approval"}


def _shell_on(ctx: ToolContext) -> bool:
    return bool(settings.ASK_SHELL_ENABLED)


@tool("run_command",
      "Run a PowerShell command on this computer and return its output (for example to query a "
      "service, list processes, run a script or export data). The user sees and approves the exact "
      "command first. Prefer the other tools when they can do the job.",
      schema(["command", "reason"], command=string_param("The PowerShell command."),
             reason=string_param("One line on why it is needed.")),
      mode="approval", label="Prepare a command", activity="writing", available=_shell_on)
def run_command(ctx: ToolContext, command: str = "", reason: str = "", **_) -> dict:
    if not command.strip():
        return {"content": "Give the command to run.", "summary": "Needs a command"}
    action = actions.propose(
        ctx.db, "run_command", "computer", "Run a command",
        {"command": command},
        preview={"fields": [{"name": reason or "Command", "before": "", "after": command}]},
        summary=(reason or command)[:120])
    ctx.drafts.append(actions.card(action))
    return {"content": f"Command prepared as change #{action.id}; it runs only when the user approves.",
            "summary": "Waiting for approval"}


# ============================================================== executors
@actions.executor("write_file")
def _do_write_file(db, args: dict) -> dict:
    target = Path(args["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.is_file():
        backup = target.with_name(f"{target.stem}.{datetime.now():%Y%m%d-%H%M%S}.bak{target.suffix}")
        shutil.copy2(target, backup)
    if args.get("mode") == "append" and target.is_file():
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(args["content"])
    else:
        target.write_text(args["content"], encoding="utf-8")
    return {"detail": f"Wrote {target}" + (f" (backup: {backup.name})" if backup else ""),
            "undo": {"path": str(target), "backup": str(backup) if backup else None}}


@actions.undoer("write_file")
def _undo_write_file(db, undo: dict) -> dict:
    target = Path(undo["path"])
    if undo.get("backup") and Path(undo["backup"]).is_file():
        shutil.copy2(undo["backup"], target)
        return {"detail": f"Restored {target.name} from its backup"}
    target.unlink(missing_ok=True)
    return {"detail": f"Removed the new file {target.name}"}


@actions.executor("run_command")
def _do_run_command(db, args: dict) -> dict:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", args["command"]],
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = ((result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"Exit code {result.returncode}: {output[:400]}")
    return {"detail": output[:600] or "Finished with no output"}
