"""
Cerebro's spoken voice.

Only a short summary of an answer is spoken (:func:`speakable`); the full
answer stays in the chat. Providers, tried in order for ``VOICE_PROVIDER=auto``:

1. **Amazon Polly** neural voices — when Bedrock is set up with AWS access keys,
   a profile or the default AWS identity. (A Bedrock API key can't call Polly.)
2. **Microsoft Edge online voices** (``edge-tts``) — free and natural, no key;
   an unofficial service, so it is only a fallback for Polly.
3. **The voice built into Windows** (System.Speech) — offline, always there.

Each provider returns audio; :func:`synthesize` can hand back WAV for the
desktop (which plays it with no extra libraries) or MP3 for the page.
"""

import asyncio
import io
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings

MAX_SPOKEN_CHARS = 420
DEFAULTS = {"polly": "Ruth", "edge": "en-US-AriaNeural", "windows": ""}

#: name -> fn(text, voice, rate) -> (bytes, mime). Tests replace entries.
PROVIDERS: Dict[str, Callable[[str, str, float], tuple]] = {}


class SpeechFailed(RuntimeError):
    pass


def speakable(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """A short, plain version of an answer, fit to be read aloud."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)          # [label](url) → label
    text = re.sub(r"\[(?:[A-Z]{1,3}\d+)\]", "", text)                   # citations [K1]
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = ""
    for sentence in sentences:
        if len(spoken) + len(sentence) > limit:
            break
        spoken += (" " if spoken else "") + sentence
    if not spoken:
        spoken = text[:limit].rsplit(" ", 1)[0] + "…"
    return spoken + " The rest is in the chat."


def order() -> List[str]:
    chosen = (settings.VOICE_PROVIDER or "auto").lower()
    if chosen == "off":
        return []
    if chosen in ("polly", "edge", "windows"):
        rest = [p for p in ("polly", "edge", "windows") if p != chosen]
        return [chosen, *rest]
    return ["polly", "edge", "windows"]


def polly_ready() -> bool:
    mode = (settings.BEDROCK_AUTH_MODE or "default").lower()
    if mode == "api_key":
        return False
    if mode == "keys":
        return bool(settings.BEDROCK_AWS_ACCESS_KEY_ID and settings.BEDROCK_AWS_SECRET_ACCESS_KEY)
    if mode == "profile":
        return bool(settings.BEDROCK_AWS_PROFILE)
    return (settings.LLM_PROVIDER or "").lower() == "bedrock"


def synthesize(text: str, fmt: str = "mp3") -> Dict[str, Any]:
    """Speak ``text``: ``{"audio": bytes, "mime", "provider"}``. Raises SpeechFailed."""
    text = (text or "").strip()
    if not text:
        raise SpeechFailed("Nothing to say")
    rate = min(2.0, max(0.5, float(settings.VOICE_RATE or 1.0)))
    problems = []
    for name in order():
        fn = PROVIDERS.get(name)
        if fn is None or (name == "polly" and "polly" not in _overridden and not polly_ready()):
            continue
        voice = settings.VOICE_NAME if (settings.VOICE_PROVIDER or "auto").lower() == name \
            and settings.VOICE_NAME else DEFAULTS[name]
        try:
            audio, mime = fn(text, voice, rate)
        except Exception as exc:  # noqa: BLE001 - try the next voice
            problems.append(f"{name}: {str(exc)[:120]}")
            continue
        if audio:
            if fmt == "wav" and mime != "audio/wav":
                audio, mime = to_wav(audio), "audio/wav"
            return {"audio": audio, "mime": mime, "provider": name}
    raise SpeechFailed("No voice could speak: " + ("; ".join(problems) or "none available"))


_overridden: set = set()


def use_provider(name: str, fn: Optional[Callable]) -> None:
    """Replace a provider (tests)."""
    if fn is None:
        _overridden.discard(name)
        PROVIDERS[name] = _BUILT_IN[name]
    else:
        _overridden.add(name)
        PROVIDERS[name] = fn


# -------------------------------------------------------------- providers
def _polly(text: str, voice: str, rate: float) -> tuple:
    from app.services.llm_service import bedrock_session

    client = bedrock_session().client("polly")
    percent = int(round(rate * 100))
    ssml = (f'<speak><prosody rate="{percent}%">'
            + text.replace("&", "and").replace("<", " ").replace(">", " ")
            + "</prosody></speak>")
    response = client.synthesize_speech(Text=ssml, TextType="ssml", OutputFormat="mp3",
                                        VoiceId=voice or DEFAULTS["polly"], Engine="neural")
    return response["AudioStream"].read(), "audio/mpeg"


def _edge(text: str, voice: str, rate: float) -> tuple:
    import edge_tts

    percent = int(round((rate - 1.0) * 100))

    async def run() -> bytes:
        communicate = edge_tts.Communicate(text, voice or DEFAULTS["edge"],
                                           rate=f"{percent:+d}%")
        chunks = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    return _run_async(run()), "audio/mpeg"


def _run_async(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    # Called from inside an event loop (an async route): run it on a thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coroutine).result(timeout=60)


def _windows(text: str, voice: str, rate: float) -> tuple:
    if sys.platform != "win32":
        raise SpeechFailed("The Windows voice is only on Windows")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "speech.wav"
        source = Path(folder) / "speech.txt"
        source.write_text(text, encoding="utf-8")
        speed = int(round((rate - 1.0) * 10))
        pick = f"$s.SelectVoice('{voice}');" if voice else ""
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"{pick}$s.Rate={max(-10, min(10, speed))};"
                  f"$s.SetOutputToWaveFile('{out}');"
                  f"$s.Speak([IO.File]::ReadAllText('{source}'));$s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not out.is_file():
            raise SpeechFailed("Windows didn't produce any speech")
        return out.read_bytes(), "audio/wav"


_BUILT_IN = {"polly": _polly, "edge": _edge, "windows": _windows}
PROVIDERS.update(_BUILT_IN)


# ------------------------------------------------------------------ audio
def to_wav(audio: bytes) -> bytes:
    """Decode MP3 (or anything PyAV reads) to 16-bit mono WAV."""
    try:
        import av
    except ImportError as exc:
        raise SpeechFailed("Can't convert speech to WAV without PyAV") from exc
    import numpy as np

    with av.open(io.BytesIO(audio)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
        pieces = []
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                pieces.append(converted.to_ndarray().reshape(-1))
        for converted in resampler.resample(None):
            pieces.append(converted.to_ndarray().reshape(-1))
    samples = np.concatenate(pieces).astype("<i2") if pieces else np.zeros(0, dtype="<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(samples.tobytes())
    return out.getvalue()


def status() -> Dict[str, Any]:
    from importlib import util

    def has(module):
        try:
            return util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    return {"provider": (settings.VOICE_PROVIDER or "auto").lower(), "order": order(),
            "polly": polly_ready(), "edge": has("edge_tts"), "windows": sys.platform == "win32"}
