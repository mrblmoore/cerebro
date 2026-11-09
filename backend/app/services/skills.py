"""
Skills: procedures Cerebro was taught, in the Agent Skills format.

A skill is a folder holding ``SKILL.md`` — YAML front matter with a ``name``
and a one-line ``description``, then the instructions in Markdown — and
optionally scripts or reference files beside it. It's the same format other
agents use, so a skill folder from elsewhere can be copied in or imported as
a zip.

Only the names and descriptions go into Ask's instructions; the full text is
read with ``use_skill`` when one is relevant, so a hundred skills cost a
hundred lines, not a hundred pages. Scripts in a skill are never run
directly — only through the approval-gated ``run_command``.
"""

import io
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.paths import CAPABILITIES_DIR

MAX_SKILL_CHARS = 40_000
PROMPT_SKILLS = 60


def root() -> Path:
    return CAPABILITIES_DIR / "skills"


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return value[:64].strip("-") or "skill"


def parse(text: str) -> Dict[str, Any]:
    """``SKILL.md`` text → ``{"name", "description", "instructions", ...}``."""
    meta: Dict[str, Any] = {}
    body = text or ""
    match = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", body, re.S)
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() and not line.startswith((" ", "\t")):
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
        body = match.group(2)
    return {**meta, "name": meta.get("name") or "", "description": meta.get("description") or "",
            "instructions": body.strip()}


def render(name: str, description: str, instructions: str, extra: Dict[str, str] = None) -> str:
    lines = ["---", f"name: {name}", f"description: {_one_line(description)}"]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {_one_line(value)}")
    return "\n".join(lines) + "\n---\n\n" + (instructions or "").strip() + "\n"


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:400]


def all_skills() -> List[Dict[str, Any]]:
    found = []
    if not root().is_dir():
        return found
    for path in sorted(root().glob("*/SKILL.md")):
        try:
            info = parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        info["name"] = info["name"] or path.parent.name
        info["slug"] = path.parent.name
        info["files"] = sorted(p.name for p in path.parent.iterdir() if p.name != "SKILL.md")
        found.append(info)
    return found


def exact(name: str) -> Optional[Dict[str, Any]]:
    """The skill saved under exactly this name (no guessing)."""
    wanted = slug(name)
    return next((s for s in all_skills() if s["slug"] == wanted), None)


def get(name: str) -> Optional[Dict[str, Any]]:
    wanted = slug(name)
    for skill in all_skills():
        if skill["slug"] == wanted or slug(skill["name"]) == wanted:
            return skill
    words = set(re.findall(r"[a-z0-9]+", (name or "").lower()))
    best = max(all_skills(), default=None,
               key=lambda s: len(words & set(re.findall(r"[a-z0-9]+", s["name"].lower()))))
    if best and words & set(re.findall(r"[a-z0-9]+", best["name"].lower())):
        return best
    return None


def save(db, name: str, description: str, instructions: str, added_by: str = "user",
         files: Dict[str, str] = None) -> Dict[str, Any]:
    """Create or replace a skill."""
    from app.services import capabilities

    if not (name or "").strip() or not (instructions or "").strip():
        raise ValueError("A skill needs a name and instructions")
    key = slug(name)
    folder = root() / key
    folder.mkdir(parents=True, exist_ok=True)
    existing = (folder / "SKILL.md").exists()
    (folder / "SKILL.md").write_text(
        render(key, description or name, instructions[:MAX_SKILL_CHARS]), encoding="utf-8")
    for filename, content in (files or {}).items():
        safe = Path(filename).name
        if safe and safe != "SKILL.md":
            (folder / safe).write_text(content, encoding="utf-8")
    capabilities._upsert(db, "skill", key, label=name.strip(), description=_one_line(description),
                         enabled=True, status="ready", error=None,
                         **({} if existing else {"added_by": added_by}))
    logger.info("addons", "Skill saved", {"name": key, "by": added_by, "replaced": existing})
    return {"name": key, "label": name.strip(), "replaced": existing}


def sync(db) -> None:
    """Rows for skill folders that were copied in by hand."""
    from app.services import capabilities

    for skill in all_skills():
        if capabilities._row(db, "skill", skill["slug"]) is None:
            capabilities._upsert(db, "skill", skill["slug"], label=skill["name"],
                                 description=_one_line(skill["description"]), enabled=True,
                                 status="ready", added_by="user")


def enabled_skills(db) -> List[Dict[str, Any]]:
    from app.models.capability import Capability

    off = {row.name for row in db.query(Capability).filter(Capability.kind == "skill",
                                                           Capability.enabled.is_(False))}
    return [s for s in all_skills() if s["slug"] not in off]


def prompt_block(db) -> str:
    """The skills list for Ask's instructions (names and one-liners only)."""
    try:
        skills = enabled_skills(db)
    except Exception:  # noqa: BLE001 - skills are optional
        return ""
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in skills[:PROMPT_SKILLS]]
    return ("Skills you were taught (procedures the user wants done their way). When a request "
            "matches one, call use_skill with its name FIRST and follow it:\n" + "\n".join(lines))


def import_archive(db, data: bytes, filename: str = "") -> List[str]:
    """Import skills from a zip holding one or more ``SKILL.md`` folders, or a bare SKILL.md."""
    names = []
    if filename.lower().endswith(".md") or not zipfile.is_zipfile(io.BytesIO(data)):
        info = parse(data.decode("utf-8", "replace"))
        name = info["name"] or Path(filename).stem
        return [save(db, name, info["description"], info["instructions"])["name"]]
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        members = [m for m in bundle.namelist() if m.rsplit("/", 1)[-1] == "SKILL.md"]
        for member in members[:50]:
            prefix = member[: -len("SKILL.md")]
            info = parse(bundle.read(member).decode("utf-8", "replace"))
            name = info["name"] or prefix.strip("/").rsplit("/", 1)[-1] or "skill"
            files = {}
            for other in bundle.namelist():
                if other.startswith(prefix) and other != member and not other.endswith("/") \
                        and "/" not in other[len(prefix):]:
                    try:
                        files[other[len(prefix):]] = bundle.read(other).decode("utf-8")
                    except UnicodeDecodeError:
                        continue          # binary helpers aren't imported
            names.append(save(db, name, info["description"], info["instructions"],
                              files=files)["name"])
    return names


def export_all() -> List[Dict[str, Any]]:
    return [{"name": s["name"], "description": s["description"],
             "instructions": s["instructions"]} for s in all_skills()]
