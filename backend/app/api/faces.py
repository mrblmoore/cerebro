"""
API for agents' faces: Live2D and VRM models the user loads.

* ``POST /api/faces`` — upload a ``.vrm`` file or a ``.zip`` of a Live2D
  model (it must contain a ``*.model3.json``). Stored under ``FACES_DIR/<id>``
  and served from ``/faces/<id>/…``.
* ``GET /api/faces/live2d/runtime`` and ``POST …/runtime`` — Live2D's Cubism
  Core is proprietary and can't ship with Cerebro. With the user's consent it
  is downloaded once from Live2D's own address into ``VENDOR_DIR``.
* ``POST /api/faces/{id}/gestures`` — add a ``.vrma`` animation (a wave, a
  nod, a cheer…) to a VRM model, played on events chosen in the agent editor.
* ``GET /api/faces/desktop`` — the default agent's face, for the desktop
  buddy and tray (which draw image frames, so Live2D and VRM fall back to
  the anime face there), plus the latest passing reaction.
* ``POST /api/faces/react`` — the app tells the desktop buddy how the face
  just reacted (happy, sorry, surprised…), so it can show it too.
* ``POST /api/faces/active`` — the app says which agent you're talking to, so
  the desktop buddy shows *that* agent's face, not only the default one's.
* ``POST /api/faces/{id}/strips`` — the app renders a VRM model into expression
  strips (the buddy draws images, not 3D) and saves them here.

Built-in models ship in ``assets/faces/vrm/<slug>/`` (``model.vrm``,
``meta.json``, ``LICENSE.txt`` and pre-rendered ``strips/``) and are addressed
as ``builtin-<slug>``; they can't be deleted.
"""

import io
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core import logger
from app.core.database import get_db
from app.core.paths import BUNDLE_ROOT, FACES_DIR, VENDOR_DIR

router = APIRouter(tags=["faces"], dependencies=[Depends(require_local_origin)])

MAX_BYTES = 100 * 1024 * 1024
CUBISM_CORE_URL = "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js"
CUBISM_FILE = "live2dcubismcore.min.js"
LIVE2D_TERMS = ("Live2D models need Live2D's Cubism Core, which Cerebro is not allowed to "
                "include. Cerebro can download it from Live2D's own website for you. By doing so "
                "you accept Live2D's Proprietary Software License; companies above Live2D's "
                "revenue threshold need a paid Live2D publication licence for commercial use.")


MAX_GESTURE_BYTES = 20 * 1024 * 1024
MAX_STRIP_BYTES = 8 * 1024 * 1024
STRIP_STATES = ("calm", "thinking", "reading", "focused", "listening", "surprised", "dizzy", "asleep",
                "happy", "sorry", "talk")
BUILTIN = "builtin-"
_reaction: Dict[str, Any] = {"emotion": None, "at": 0.0}
#: The agent you're talking to in the app (the desktop buddy follows it).
_active: Dict[str, Any] = {"agent_id": None, "at": 0.0}


def builtin_dir() -> Path:
    return BUNDLE_ROOT / "assets" / "faces" / "vrm"


def _builtin_meta(folder: Path) -> Dict[str, Any]:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not (folder / "model.vrm").is_file():
        return {}
    return {"id": BUILTIN + folder.name, "kind": "vrm", "entry": "model.vrm", "builtin": True,
            "name": meta.get("name") or folder.name, "author": meta.get("author"),
            "license": meta.get("license"), "source_url": meta.get("source_url"),
            "preview": "preview.png" if (folder / "preview.png").is_file() else None,
            "framing": meta.get("framing"), "expressions": meta.get("expressions_map")}


def builtin_faces() -> List[Dict[str, Any]]:
    root = builtin_dir()
    if not root.is_dir():
        return []
    return [m for m in (_builtin_meta(f) for f in sorted(root.iterdir()) if f.is_dir()) if m]


def strips_dir(face_id: str) -> Optional[Path]:
    """Rendered expression strips for a VRM face, if there are any."""
    # Strips the app rendered (with your framing and expression choices) win over
    # the ones shipped with a built-in model.
    candidates = [FACES_DIR / face_id / "strips"]
    if face_id.startswith(BUILTIN):
        candidates.append(builtin_dir() / face_id[len(BUILTIN):] / "strips")
    for folder in candidates:
        if (folder / "face.json").is_file():
            return folder
    return None


def _meta(folder: Path) -> Dict[str, Any]:
    try:
        meta = json.loads((folder / "face.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    gestures = folder / "gestures"
    if meta.get("kind") == "vrm" and gestures.is_dir():
        meta["gestures"] = sorted(f"gestures/{p.name}" for p in gestures.glob("*.vrma"))
    return meta


def _folder(face_id: str) -> Path:
    if face_id.startswith(BUILTIN):
        root = builtin_dir().resolve()
        folder = (root / face_id[len(BUILTIN):]).resolve()
        if root not in folder.parents or not folder.is_dir():
            raise HTTPException(status_code=404, detail="No such model")
        return folder
    folder = (FACES_DIR / face_id).resolve()
    if FACES_DIR.resolve() not in folder.parents or not folder.is_dir():
        raise HTTPException(status_code=404, detail="No such model")
    return folder


@router.get("/api/faces")
def list_faces() -> Dict[str, Any]:
    items = builtin_faces()
    if FACES_DIR.is_dir():
        for folder in sorted(FACES_DIR.iterdir()):
            meta = _meta(folder)
            if meta:
                items.append(meta)
    return {"faces": items, "live2d_terms": LIVE2D_TERMS}


def safe_extract(data: bytes, target: Path) -> None:
    """Unzip into ``target``, refusing any entry that would land outside it."""
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        total = 0
        for info in bundle.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or (name.parts and ":" in name.parts[0]):
                raise ValueError(f"Unsafe path in the zip: {info.filename}")
            total += info.file_size
            if total > MAX_BYTES * 3:
                raise ValueError("The model is too large once unzipped.")
        for info in bundle.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            destination = (target / Path(*name.parts)).resolve()
            if target.resolve() not in destination.parents and destination != target.resolve():
                raise ValueError(f"Unsafe path in the zip: {info.filename}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)


@router.post("/api/faces")
async def upload_face(file: UploadFile = File(...)) -> Dict[str, Any]:
    data = await file.read()
    name = file.filename or "model"
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="That model is over 100 MB.")
    face_id = uuid.uuid4().hex[:10]
    folder = FACES_DIR / face_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if name.lower().endswith(".vrm") or data[:4] == b"glTF":
            if data[:4] != b"glTF":
                raise ValueError("That isn't a VRM file.")
            (folder / "model.vrm").write_bytes(data)
            meta = {"id": face_id, "kind": "vrm", "entry": "model.vrm", "name": Path(name).stem}
        elif zipfile.is_zipfile(io.BytesIO(data)):
            safe_extract(data, folder)
            entries = sorted(p for p in folder.rglob("*.model3.json"))
            if not entries:
                raise ValueError("No *.model3.json in that zip — it needs a Cubism 3/4 Live2D model.")
            entry = entries[0].relative_to(folder).as_posix()
            meta = {"id": face_id, "kind": "live2d", "entry": entry, "name": Path(name).stem}
        else:
            raise ValueError("Load a .vrm file, or a .zip of a Live2D model.")
    except ValueError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc))
    except zipfile.BadZipFile:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="That zip can't be opened.")
    (folder / "face.json").write_text(json.dumps(meta), encoding="utf-8")
    logger.info("faces", "Face model loaded", meta)
    return meta


@router.post("/api/faces/{face_id}/gestures")
async def upload_gesture(face_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """A VRM Animation (.vrma) for a VRM model: a wave, a nod, a cheer."""
    if face_id.startswith(BUILTIN):
        raise HTTPException(status_code=400, detail="Gestures can be added to models you load, not built-in ones.")
    folder = _folder(face_id)
    if _meta(folder).get("kind") != "vrm":
        raise HTTPException(status_code=400, detail="Gestures are for VRM models.")
    data = await file.read(MAX_GESTURE_BYTES + 1)
    if len(data) > MAX_GESTURE_BYTES:
        raise HTTPException(status_code=400, detail="That gesture is over 20 MB.")
    if data[:4] != b"glTF":
        raise HTTPException(status_code=400, detail="That isn't a .vrma file.")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(file.filename or "gesture").stem).strip("-")[:40] or "gesture"
    target = folder / "gestures" / f"{stem}.vrma"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(data)
    logger.info("faces", "Gesture added", {"face": face_id, "gesture": target.name})
    return {"ok": True, "path": f"gestures/{target.name}", "face": _meta(folder)}


@router.post("/api/faces/react")
def react(body: Dict[str, Any]) -> Dict[str, Any]:
    emotion = str((body or {}).get("emotion") or "")
    if emotion not in ("happy", "wink", "love", "sorry", "smug", "surprised", "pout"):
        raise HTTPException(status_code=400, detail="Unknown reaction")
    _reaction.update(emotion=emotion, at=time.time())
    return {"ok": True}


@router.delete("/api/faces/{face_id}")
def delete_face(face_id: str) -> Dict[str, Any]:
    if face_id.startswith(BUILTIN):
        raise HTTPException(status_code=400, detail="Built-in models can't be removed.")
    folder = (FACES_DIR / face_id).resolve()
    if FACES_DIR.resolve() not in folder.parents:
        raise HTTPException(status_code=400, detail="Bad id")
    shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@router.get("/faces/{face_id}/{path:path}")
def face_file(face_id: str, path: str):
    if face_id.startswith(BUILTIN):
        base = builtin_dir().resolve()
        root = (base / face_id[len(BUILTIN):]).resolve()
    else:
        base = FACES_DIR.resolve()
        root = (base / face_id).resolve()
    target = (root / path).resolve()
    if base not in root.parents or root not in target.parents or not target.is_file():
        # Strips the app rendered for a built-in model live in the user's data folder.
        rendered = strips_dir(face_id)
        if rendered is not None and path.startswith("strips/"):
            candidate = (rendered / path[len("strips/"):]).resolve()
            if rendered.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


@router.post("/api/faces/{face_id}/strips")
async def save_strips(face_id: str, request: Request) -> Dict[str, Any]:
    """Expression strips the app rendered from a VRM model, for the desktop buddy.

    Multipart: ``manifest`` (the face.json text) and one PNG per state."""
    _folder(face_id)
    form = await request.form()
    try:
        manifest = json.loads(str(form.get("manifest") or "{}"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad manifest")
    states = manifest.get("states") or {}
    if not states or not set(states) <= set(STRIP_STATES) or "calm" not in states:
        raise HTTPException(status_code=400, detail="The strips need at least calm, and only known states.")
    target = FACES_DIR / face_id / "strips"
    target.mkdir(parents=True, exist_ok=True)
    clean = {"frame": {"width": int(manifest.get("frame", {}).get("width") or 128),
                       "height": int(manifest.get("frame", {}).get("height") or 128)}, "states": {}}
    for state, info in states.items():
        upload = form.get(state)
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail=f"Missing the {state} strip")
        data = await upload.read(MAX_STRIP_BYTES + 1)
        if len(data) > MAX_STRIP_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise HTTPException(status_code=400, detail=f"The {state} strip isn't a PNG")
        (target / f"{state}.png").write_bytes(data)
        clean["states"][state] = {"frames": max(1, min(60, int(info.get("frames") or 1))),
                                  "ms": max(40, min(1000, int(info.get("ms") or 150))), "file": f"{state}.png"}
        if state == "talk" and isinstance(info.get("levels"), list):
            clean["states"][state]["levels"] = [float(x) for x in info["levels"]][:12]
    (target / "face.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")
    logger.info("faces", "Desktop strips saved", {"face": face_id, "states": len(clean["states"])})
    return {"ok": True, "states": sorted(clean["states"])}


@router.post("/api/faces/active")
def set_active(body: Dict[str, Any]) -> Dict[str, Any]:
    """The agent you're talking to; the desktop buddy shows its face."""
    agent_id = (body or {}).get("agent_id")
    _active.update(agent_id=int(agent_id) if str(agent_id or "").isdigit() else None, at=time.time())
    return {"ok": True}


# ------------------------------------------------------ Live2D runtime
@router.get("/api/faces/live2d/runtime")
def live2d_runtime() -> Dict[str, Any]:
    return {"installed": (VENDOR_DIR / CUBISM_FILE).is_file(), "terms": LIVE2D_TERMS,
            "source": CUBISM_CORE_URL}


@router.post("/api/faces/live2d/runtime")
def install_live2d_runtime(body: Dict[str, Any]) -> Dict[str, Any]:
    if not body.get("agree"):
        raise HTTPException(status_code=400, detail="Agree to Live2D's terms first.")
    import requests

    try:
        response = requests.get(CUBISM_CORE_URL, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't download it from Live2D: {exc}")
    if b"Live2DCubismCore" not in response.content[:200000]:
        raise HTTPException(status_code=502, detail="Live2D's site didn't send the expected file.")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    (VENDOR_DIR / CUBISM_FILE).write_bytes(response.content)
    logger.info("faces", "Live2D Cubism Core downloaded with the user's consent", {})
    return {"installed": True}


@router.post("/api/faces/live2d/runtime/file")
async def import_live2d_runtime(file: UploadFile = File(...), agree: bool = Form(False)) -> Dict[str, Any]:
    """Live2D's runtime from a file the user downloaded themselves (for networks
    that block Live2D's site). Same terms as the download."""
    if not agree:
        raise HTTPException(status_code=400, detail="Agree to Live2D's terms first.")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or b"Live2DCubismCore" not in data[:200000]:
        raise HTTPException(status_code=400, detail="That isn't live2dcubismcore.min.js from Live2D's Cubism SDK for Web.")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    (VENDOR_DIR / CUBISM_FILE).write_bytes(data)
    logger.info("faces", "Live2D Cubism Core imported from a file with the user's consent", {})
    return {"installed": True}


@router.get("/api/faces/live2d/runtime.js")
def live2d_runtime_js():
    path = VENDOR_DIR / CUBISM_FILE
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not installed")
    return FileResponse(path, media_type="text/javascript")


# ------------------------------------------------------------ desktop
@router.get("/api/faces/desktop")
def desktop_face(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The face the desktop buddy and tray show: the agent you're talking to in
    the app (else the default agent). A VRM face with rendered strips is shown
    as itself; otherwise Live2D and VRM fall back to the anime face."""
    from app.services import agents

    agent = agents.get(db, _active["agent_id"]) if _active["agent_id"] else None
    if agent is None or getattr(agent, "archived", False):
        agent = agents.default_agent(db)
    face = agent.face or "brain"
    style = agent.style()
    fresh = time.time() - _reaction["at"] < 3
    out = {"face": "anime" if face in ("live2d", "vrm") else face,
           "palette": style.get("palette") or ("silver" if face in ("live2d", "vrm") else "pink"),
           "hair": style.get("hair") or "long", "agent": agent.name, "agent_id": agent.id,
           "react": _reaction["emotion"] if fresh else None, "strips": None}
    if face == "vrm" and style.get("model"):
        rendered = strips_dir(str(style["model"]))
        if rendered is not None:
            out.update(face="strips", strips=str(rendered.resolve()))
    return out
