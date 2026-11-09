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
"""

import io
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core import logger
from app.core.database import get_db
from app.core.paths import FACES_DIR, VENDOR_DIR

router = APIRouter(tags=["faces"], dependencies=[Depends(require_local_origin)])

MAX_BYTES = 100 * 1024 * 1024
CUBISM_CORE_URL = "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js"
CUBISM_FILE = "live2dcubismcore.min.js"
LIVE2D_TERMS = ("Live2D models need Live2D's Cubism Core, which Cerebro is not allowed to "
                "include. Cerebro can download it from Live2D's own website for you. By doing so "
                "you accept Live2D's Proprietary Software License; companies above Live2D's "
                "revenue threshold need a paid Live2D publication licence for commercial use.")


MAX_GESTURE_BYTES = 20 * 1024 * 1024
_reaction: Dict[str, Any] = {"emotion": None, "at": 0.0}


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
    folder = (FACES_DIR / face_id).resolve()
    if FACES_DIR.resolve() not in folder.parents or not folder.is_dir():
        raise HTTPException(status_code=404, detail="No such model")
    return folder


@router.get("/api/faces")
def list_faces() -> Dict[str, Any]:
    items = []
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
    folder = (FACES_DIR / face_id).resolve()
    if FACES_DIR.resolve() not in folder.parents:
        raise HTTPException(status_code=400, detail="Bad id")
    shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@router.get("/faces/{face_id}/{path:path}")
def face_file(face_id: str, path: str):
    root = (FACES_DIR / face_id).resolve()
    target = (root / path).resolve()
    if FACES_DIR.resolve() not in root.parents or root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


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


@router.get("/api/faces/live2d/runtime.js")
def live2d_runtime_js():
    path = VENDOR_DIR / CUBISM_FILE
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not installed")
    return FileResponse(path, media_type="text/javascript")


# ------------------------------------------------------------ desktop
@router.get("/api/faces/desktop")
def desktop_face(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The face the desktop buddy and tray show: the default agent's."""
    from app.services import agents

    agent = agents.default_agent(db)
    face = agent.face or "brain"
    style = agent.style()
    fresh = time.time() - _reaction["at"] < 3
    return {"face": "anime" if face in ("live2d", "vrm") else face,
            "palette": style.get("palette") or ("silver" if face in ("live2d", "vrm") else "pink"),
            "hair": style.get("hair") or "long", "agent": agent.name,
            "react": _reaction["emotion"] if fresh else None}
