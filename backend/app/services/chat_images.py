"""
Images in chat — a user's upload, and images Cerebro pulls out of a document.

Everything is stored flat under ``data/chat_images`` by a generated name, never
the name the browser/OS gave it, so a request can never turn into an arbitrary
path on disk. Two directions:

* ``save_upload`` — someone attaches a screenshot or photo to a chat message.
* ``extract_document_images`` — Cerebro pulls the images actually embedded in
  a Word/PowerPoint/PDF file so an answer can point at "the diagram on page 3"
  rather than only quoting text near it.

Neither requires the document to have been indexed — like the rest of ambient
document awareness, this reads what is already on disk.
"""

import hashlib
import mimetypes
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from app.core import logger
from app.core.paths import CHAT_IMAGES_DIR

#: What Cerebro will store and (later) show. Kept narrow: SVG or anything
#: executable-adjacent has no business being displayed inline.
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

MAX_BYTES = 15 * 1024 * 1024   # 15 MB — generous for a screenshot, not for a video


class ImageError(RuntimeError):
    """Raised with a message intended to be shown to the user."""


def _safe_extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in ALLOWED_EXTENSIONS else ".png"


def save_upload(data: bytes, filename: str = "upload.png") -> str:
    """
    Persist uploaded image bytes, returning the stored name (not a path).

    Named by content hash rather than a random ID: the same bytes always land
    on the same file. A user upload is one-shot either way, but document
    image extraction re-runs on every question against the same document —
    without this, that would write a fresh duplicate of the same diagram to
    disk on every answer instead of reusing the one already there.
    """
    if not data:
        raise ImageError("The image was empty.")
    if len(data) > MAX_BYTES:
        raise ImageError(f"That image is too big ({len(data) // 1_000_000} MB, max 15 MB).")

    CHAT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    stored_name = f"{digest}{_safe_extension(filename)}"
    stored_path = CHAT_IMAGES_DIR / stored_name
    if not stored_path.exists():
        stored_path.write_bytes(data)
    return stored_name


def resolve(stored_name: str) -> Path:
    """
    The full path for a name previously returned by ``save_upload``.

    Rejects anything that is not a bare filename Cerebro itself generated —
    no path separators, no "..". This is what stands between the image
    endpoint and someone asking for an arbitrary file on disk.
    """
    name = Path(stored_name or "").name
    if not name or name != stored_name:
        raise ImageError("Not a valid stored image name.")
    path = CHAT_IMAGES_DIR / name
    if not path.is_file():
        raise ImageError("That image is no longer available.")
    return path


def mime_type(stored_name: str) -> str:
    return mimetypes.guess_type(stored_name)[0] or "application/octet-stream"


# ------------------------------------------------------- document extraction
def extract_document_images(path: Path, kind: str, limit: int = 2) -> List[Dict[str, Any]]:
    """
    Images embedded in a document, saved the same way an upload is.

    docx/pptx are zip archives with a conventional ``word/media`` /
    ``ppt/media`` folder — reading that needs no extra dependency beyond the
    ``python-docx``/``python-pptx`` Cerebro already requires for text. PDFs are
    read with ``pypdf``'s own image extraction, when the installed version
    supports it; older pypdf versions silently yield nothing rather than error.
    """
    try:
        if kind == "docx":
            return _extract_from_zip(path, "word/media/", limit)
        if kind == "pptx":
            return _extract_from_zip(path, "ppt/media/", limit)
        if kind == "pdf":
            return _extract_from_pdf(path, limit)
    except Exception as exc:  # noqa: BLE001 - a missing image is never fatal
        logger.warn("chat_images", "Document image extraction failed",
                   {"path": str(path), "error": str(exc)})
    return []


def _extract_from_zip(path: Path, media_prefix: str, limit: int) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        media_names = sorted(
            name for name in archive.namelist()
            if name.startswith(media_prefix) and Path(name).suffix.lower() in ALLOWED_EXTENSIONS
        )
        for name in media_names[:limit]:
            data = archive.read(name)
            stored = save_upload(data, filename=Path(name).name)
            found.append({"image": stored, "caption": f"Image from {path.name}"})
    return found


def _extract_from_pdf(path: Path, limit: int) -> List[Dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    found: List[Dict[str, Any]] = []
    reader = PdfReader(str(path))
    for number, page in enumerate(reader.pages, start=1):
        images = getattr(page, "images", None)
        if not images:
            continue
        for image in images:
            if len(found) >= limit:
                return found
            try:
                stored = save_upload(image.data, filename=image.name or "page.png")
            except Exception:
                continue
            found.append({"image": stored, "caption": f"{path.name}, page {number}"})
    return found
