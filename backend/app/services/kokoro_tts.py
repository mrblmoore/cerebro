"""
Kokoro — a natural voice that runs on this PC, with nothing online.

Kokoro-82M (Apache-2.0, by hexgrad) is a small neural text-to-speech model.
Cerebro ships its quantised ONNX export and a handful of its voices, and runs it
with onnxruntime (already bundled). Text becomes phonemes with
:mod:`app.services.g2p` — Cerebro's own converter over misaki's dictionaries —
so no eSpeak (GPL) and no downloads are involved.

Voices are files ``voices/<name>.bin``: 510 × 1 × 256 float32 style vectors
(one row per phoneme count), as published in the official ONNX export. A voice
may be a blend: ``af_heart+af_bella`` (equal parts) or
``af_heart:0.7,af_bella:0.3``.

Expressive cues (see :mod:`app.services.tts`) are performed by speaking each
part on its own — faster when excited, slower and quieter when whispering —
and joining the parts with short gaps.
"""

import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core import logger
from app.core.paths import BUNDLED_MODELS_DIR, MODELS_DIR

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
MODEL_FILES = ("model_quantized.onnx", "model_q8f16.onnx", "model_fp16.onnx", "model.onnx")
DEFAULT_VOICE = "af_heart"
#: What each bundled voice sounds like, for the voice picker.
VOICE_LABELS = {
    "af_heart": "Heart — warm, bright (US)", "af_bella": "Bella — sweet, expressive (US)",
    "af_nicole": "Nicole — soft, breathy (US)", "af_sky": "Sky — light, young (US)",
    "af_nova": "Nova — clear, friendly (US)", "af_sarah": "Sarah — calm (US)",
    "bf_emma": "Emma — gentle (UK)", "bf_isabella": "Isabella — lively (UK)",
    "am_michael": "Michael — warm (US, male)", "bm_george": "George — steady (UK, male)",
}
#: Cue → (speed multiplier, loudness multiplier).
STYLE_PERFORMANCE = {
    "excited": (1.12, 1.1), "slow": (0.85, 1.0), "tease": (0.94, 1.0), "whisper": (0.9, 0.42),
    "soft": (0.94, 0.7), "emphasis": (0.95, 1.18),
}
SOUND_WORDS = {"giggle": "hehe", "laugh": "haha!", "gasp": "oh!", "sigh": "haah…", "hmm": "hmm…",
               "yay": "yay!"}


class KokoroUnavailable(RuntimeError):
    pass


def folders() -> List[Path]:
    """Where Kokoro's files are looked for: shipped with Cerebro, then added by the user."""
    return [BUNDLED_MODELS_DIR / "kokoro", MODELS_DIR / "kokoro"]


def model_path() -> Optional[Path]:
    for folder in folders():
        for name in MODEL_FILES:
            if (folder / name).is_file():
                return folder / name
    return None


def voice_files() -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for folder in folders():
        for path in sorted((folder / "voices").glob("*.bin")) if (folder / "voices").is_dir() else []:
            found.setdefault(path.stem, path)
    return found


def available() -> Tuple[bool, str]:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "The offline voice needs onnxruntime, which isn't in this copy of Cerebro."
    if model_path() is None:
        return False, "The offline voice (Kokoro) isn't installed in this copy of Cerebro."
    if not voice_files():
        return False, "The offline voice (Kokoro) has no voices installed."
    return True, ""


def voices() -> List[Dict[str, str]]:
    return [{"id": name, "label": VOICE_LABELS.get(name, name)} for name in voice_files()]


_session = None
_session_path: Optional[Path] = None
_styles: Dict[str, Any] = {}
_lock = threading.Lock()


def _engine():
    global _session, _session_path
    path = model_path()
    if path is None:
        raise KokoroUnavailable(available()[1])
    with _lock:
        if _session is None or _session_path != path:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.log_severity_level = 3
            _session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
            _session_path = path
            logger.info("voice", "Offline voice loaded", {"model": path.name})
        return _session


def _style_table(name: str):
    import numpy as np

    if name not in _styles:
        files = voice_files()
        if name not in files:
            raise KokoroUnavailable(f"The offline voice “{name}” isn't installed.")
        _styles[name] = np.fromfile(files[name], dtype=np.float32).reshape(-1, 1, 256)
    return _styles[name]


def blend(voice: str):
    """A voice name or blend → its style table (rows × 1 × 256)."""
    import numpy as np

    parts: List[Tuple[str, float]] = []
    for piece in re.split(r"[+,]", voice or DEFAULT_VOICE):
        name, _, weight = piece.strip().partition(":")
        if name:
            parts.append((name, float(weight) if weight else 1.0))
    if not parts:
        parts = [(DEFAULT_VOICE, 1.0)]
    total = sum(w for _, w in parts) or 1.0
    return sum(_style_table(name) * (w / total) for name, w in parts).astype(np.float32)


def _chunks(phonemes: str, limit: int = MAX_PHONEMES) -> List[str]:
    """Split at sentence, then clause, then word boundaries to fit the model."""
    if len(phonemes) <= limit:
        return [phonemes] if phonemes.strip() else []
    pieces: List[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", phonemes):
        while len(sentence) > limit:
            cut = max(sentence.rfind(p, 0, limit) for p in (",", ";", ":", "—", " "))
            cut = cut + 1 if cut > 0 else limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence.strip():
            pieces.append(sentence.strip())
    return pieces


def synthesize_phonemes(phonemes: str, voice: str = DEFAULT_VOICE, speed: float = 1.0):
    """Phonemes → float32 audio at 24 kHz."""
    import numpy as np

    session = _engine()
    table = blend(voice)
    vocab = token_ids()
    out = []
    for chunk in _chunks(phonemes):
        ids = [vocab[c] for c in chunk if c in vocab]
        if not ids:
            continue
        style = table[min(len(ids), len(table)) - 1]
        audio = session.run(None, {
            "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
            "style": style.astype(np.float32),
            "speed": np.array([max(0.5, min(2.0, speed))], dtype=np.float32)})[0]
        out.append(np.asarray(audio, dtype=np.float32).ravel())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def _limit(audio, ceiling: float = 0.95):
    """Keep a part under full scale, so a cue's loudness change is heard, not clipped."""
    import numpy as np

    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return audio * (ceiling / peak) if peak > ceiling else audio


def _silence(seconds: float):
    import numpy as np

    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def speak(text: str, voice: str = None, style: Dict[str, Any] = None) -> bytes:
    """Text with Cerebro's sound cues → WAV bytes, performed offline."""
    import numpy as np

    from app.services import g2p, tts

    style = style or {}
    voice = voice or DEFAULT_VOICE
    rate = float(style.get("rate") or 1.0)
    volume_db = float(style.get("volume") or 0)
    parts = []
    for kind, value, styles in tts.segments(text):
        if kind == "sound":
            if value == "pause":
                parts.append(_silence(0.35))
                continue
            phonemes = g2p.phonemize(SOUND_WORDS.get(value, value))
            parts.append(_limit(synthesize_phonemes(phonemes, voice, rate * 1.08)))
            parts.append(_silence(0.08))
            continue
        speed, gain = STYLE_PERFORMANCE.get(styles[0], (1.0, 1.0)) if styles else (1.0, 1.0)
        phonemes = g2p.phonemize(value)
        if not phonemes.strip():
            continue
        audio = _limit(synthesize_phonemes(phonemes, voice, rate * speed)) * gain
        parts.append(audio)
        parts.append(_silence(0.06))
    if not parts:
        raise KokoroUnavailable("Nothing to say.")
    audio = np.concatenate(parts) * (10 ** (volume_db / 20))
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return tts.pcm_to_wav(pcm, SAMPLE_RATE)


_vocab: Dict[str, int] = {}


def token_ids() -> Dict[str, int]:
    """Kokoro's phoneme → token id table (from the model's config.json, Apache-2.0)."""
    if not _vocab:
        import json

        _vocab.update(json.loads((Path(__file__).with_name("g2p_data") / "kokoro_vocab.json")
                                 .read_text(encoding="utf-8")))
    return _vocab
