"""
Speech to text, on this PC.

faster-whisper turns a recording into text locally — nothing is uploaded to
be transcribed. The model (``VOICE_MODEL``, ``base.en`` by default) is
downloaded once, on first use, to ``…\\Cerebro\\models``; silence is trimmed
with the Silero voice-activity filter built into faster-whisper.

Words Cerebro already knows — the open case, recent case numbers, people it
has messaged, the systems and add-ons it is connected to — are given to the
model as hints, so "reply to Priya about CAS zero one two three four" comes out
spelled the way Cerebro needs it.
"""

import io
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from app.core import logger
from app.core.config import settings
from app.core.paths import MODELS_DIR

_model = None
_model_name = ""
_lock = threading.Lock()
#: Replaced in tests (and by anything else that wants a different engine).
_transcriber: Optional[Callable[[bytes, str], str]] = None


class SpeechUnavailable(RuntimeError):
    pass


def installed() -> bool:
    from importlib import util

    try:
        return util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def available() -> bool:
    return _transcriber is not None or installed()


def set_transcriber(fn: Optional[Callable[[bytes, str], str]]) -> None:
    global _transcriber
    _transcriber = fn


def model():
    """The Whisper model, loaded (and downloaded the first time) once."""
    global _model, _model_name
    wanted = (settings.VOICE_MODEL or "base.en").strip()
    with _lock:
        if _model is not None and _model_name == wanted:
            return _model
        if not installed():
            raise SpeechUnavailable("Speech recognition isn't installed in this copy of Cerebro "
                                    "(pip install -r desktop/requirements-voice.txt).")
        from faster_whisper import WhisperModel

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("voice", "Loading the speech model", {"model": wanted})
        _model = WhisperModel(wanted, device="cpu", compute_type="int8",
                              download_root=str(MODELS_DIR / "whisper"))
        _model_name = wanted
        return _model


def warm_up() -> None:
    """Load the model in the background so the first request is quick."""
    if installed() and settings.VOICE_ENABLED:
        threading.Thread(target=lambda: _safe(model), daemon=True, name="cerebro-voice-load").start()


def _safe(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - loading is retried on first use
        logger.warn("voice", "Speech model didn't load", {"error": str(exc)[:200]})


def hints(db=None, context: Dict[str, Any] = None) -> str:
    """A short list of words to expect, for the model's ``initial_prompt``."""
    words: List[str] = ["Cerebro"]
    context = context or {}
    if context.get("crm_case"):
        words.append(str(context["crm_case"]))
    if context.get("customer"):
        words.append(str(context["customer"]))
    try:
        from app.services import browser

        words += [c.label for c in browser.connectors() if c.enabled]
    except Exception:  # noqa: BLE001
        pass
    if db is not None:
        try:
            from app.models.capability import Capability
            from app.models.case import Case
            from app.models.enterprise import EnterpriseMessage

            words += [c.label or c.name for c in db.query(Capability).limit(20)]
            words += [c.case_id for c in db.query(Case).order_by(Case.id.desc()).limit(8) if getattr(c, "case_id", None)]
            senders = (db.query(EnterpriseMessage.sender_name)
                       .order_by(EnterpriseMessage.id.desc()).limit(40))
            words += [row[0] for row in senders if row[0]]
        except Exception:  # noqa: BLE001 - hints are a nicety
            pass
    seen, unique = set(), []
    for word in words:
        key = (word or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(word.strip())
    return ", ".join(unique)[:600]


def transcribe(audio: bytes, prompt: str = "") -> Dict[str, Any]:
    """Audio (WAV, WebM, OGG, MP3…) → ``{"text", "duration"}``."""
    if not audio:
        return {"text": "", "duration": 0}
    if _transcriber is not None:
        return {"text": normalise(_transcriber(audio, prompt)), "duration": None}
    whisper = model()
    segments, info = whisper.transcribe(
        io.BytesIO(audio), language="en" if (settings.VOICE_MODEL or "").endswith(".en") else None,
        beam_size=1, vad_filter=True, initial_prompt=prompt or None,
        condition_on_previous_text=False)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": normalise(text), "duration": round(getattr(info, "duration", 0) or 0, 1)}


# -------------------------------------------------------- tidy the text
_DIGITS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "three": "3",
           "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|\d)"
_CASE = re.compile(rf"\b(CAS|case number|ticket number|ticket)[\s,.-]*((?:{_DIGIT_WORD}[\s,.-]*){{3,12}})",
                   re.IGNORECASE)


def _spoken_digits(text: str) -> str:
    parts = re.findall(r"\d+|[a-z]+", text.lower())
    return "".join(_DIGITS.get(p, p) for p in parts if p.isdigit() or p in _DIGITS)


def normalise(text: str) -> str:
    """Spoken case numbers as Cerebro writes them ("CAS zero one two" → "CAS-012")."""
    text = (text or "").strip()

    def case(match):
        prefix = match.group(1)
        digits = _spoken_digits(match.group(2))
        trailing = " " if match.group(2).rstrip() != match.group(2) else ""
        if prefix.upper() == "CAS":
            return f"CAS-{digits}{trailing}"
        return f"{prefix} {digits}{trailing}"

    return _CASE.sub(case, text).strip()


# ---------------------------------------------------------- what was said
_YES = r"(?:yes|yeah|yep|yup|sure|ok|okay|alright|all right)"
_APPROVE = re.compile(
    rf"^(?:{_YES}[,.! ]*)?(?:please[,. ]*)?(?:approve(?: it| that| the change)?|send(?: it| that| this| the (?:draft|message|email|reply))?|"
    rf"go ahead|do it|confirm(?: it)?|that's (?:fine|good|right)|looks good)(?:[,. ]*(?:please|thanks|thank you))?[.!]*$"
    rf"|^{_YES}[.!]*$", re.IGNORECASE)
_DISCARD = re.compile(r"^(?:no[,. ]*)?(?:cancel|discard|delete|don't send(?: it)?|scrap)(?: it| that| this| the draft)?"
                      r"(?:[,. ]*(?:please|thanks))?[.!]*$|^no[.!]*$", re.IGNORECASE)
_STOP = re.compile(r"^(?:stop|quiet|be quiet|shut up|hush|never ?mind|that's all)(?: talking)?[.!]*$",
                   re.IGNORECASE)


def spoken_intent(text: str) -> Optional[str]:
    """``approve`` / ``discard`` / ``stop`` for a short spoken decision, else ``None``."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean or len(clean) > 60:
        return None
    if _STOP.match(clean):
        return "stop"
    if _APPROVE.match(clean):
        return "approve"
    if _DISCARD.match(clean):
        return "discard"
    return None
