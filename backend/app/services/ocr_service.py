"""Local OCR for scanned PDFs and approved activity screenshots.

RapidOCR is bundled for the normal installer and runs fully on-device.  The
fallback remains optional so a minimal source install still starts cleanly.
"""

from importlib import util
from pathlib import Path
from typing import Dict, Iterable, List

_ENGINE = None


def available() -> bool:
    return util.find_spec("rapidocr_onnxruntime") is not None and util.find_spec("fitz") is not None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def image_text(image) -> str:
    if not available():
        return ""
    try:
        result, _elapsed = _engine()(image)
    except Exception:
        return ""
    if not result:
        return ""
    return "\n".join(str(item[1]).strip() for item in result
                     if len(item) > 1 and str(item[1]).strip())


def pdf_pages(path: Path, page_numbers: Iterable[int], max_pages: int = 40) -> Dict[int, str]:
    """OCR selected one-based PDF pages and return their extracted text."""
    if not available():
        return {}
    import fitz

    wanted = list(dict.fromkeys(int(number) for number in page_numbers if int(number) > 0))[:max_pages]
    extracted: Dict[int, str] = {}
    document = fitz.open(str(path))
    try:
        for number in wanted:
            if number > len(document):
                continue
            page = document[number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            text = image_text(pixmap.tobytes("png"))
            if text.strip():
                extracted[number] = text.strip()
    finally:
        document.close()
    return extracted


def status() -> Dict[str, object]:
    return {
        "ok": available(),
        "enabled": available(),
        "detail": ("Local OCR ready" if available() else
                   "OCR components are not installed; text documents still work"),
    }

