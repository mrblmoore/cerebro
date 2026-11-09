"""
Cerebro's spoken voice — expressive, and shaped by the agent's personality.

Only a short summary of an answer is spoken (:func:`speakable`); the full
answer stays in the chat. ``VOICE_PROVIDER=auto`` ("Best available") uses
voices that work on any network first:

1. **Kokoro** — a natural voice that runs on this PC and ships with Cerebro
   (:mod:`app.services.kokoro_tts`). Works offline and behind any firewall.
2. **The voice built into Windows** — offline, always there, robotic.
3. Online voices, only if both of those fail.

Online voices, chosen in Settings → Voice (any of them may be blocked on a
work network; the error then says which address couldn't be reached):

* **Amazon Polly** — with the AWS access keys, a profile or the default AWS
   identity set for Bedrock (a Bedrock API key can't call Polly). Engines:
   *generative* (most natural and emotional), *neural*, *long-form*, and
   *standard* (the one with special effects: whispers, soft voice, pitch,
   timbre, breaths). If a voice lacks the engine, Cerebro steps down.
* **Amazon Nova Sonic** — when chosen (``VOICE_PROVIDER=sonic``): Bedrock's
   speech-to-speech model reads the reply with natural, expressive delivery
   (see :mod:`app.services.sonic`).
  It goes through Amazon Bedrock, so it works wherever Bedrock does.
* **Microsoft Edge online voices** (``edge-tts``) — free, no key; needs
  speech.platform.bing.com.

**Sound cues.** Spoken text can carry little reactions:

* sounds: ``[giggle] [laugh] [gasp] [sigh] [hmm] [yay] [pause]``;
* spans: ``[whisper]…[/whisper] [soft]… [excited]… [slow]… [emphasis]… [tease]…``.

Each provider performs them as well as it can (:func:`render_ssml` for Polly,
per engine; stage directions for Nova Sonic; spoken interjections and
prosody for Edge and Windows), and they never show in the chat
(:func:`strip_cues`). Unsupported markup never reaches a service, and if Polly
still rejects the SSML it is retried plainer, so speech never fails over a tag.
"""

import asyncio
import inspect
import io
import re
import subprocess
import sys
import tempfile
import time
import wave
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import logger
from app.core.config import settings

MAX_SPOKEN_CHARS = 420
DEFAULTS = {"kokoro": "af_heart", "polly": "Ruth", "sonic": "tiffany", "edge": "en-US-AriaNeural",
            "windows": ""}
ALL = ("kokoro", "polly", "sonic", "edge", "windows")
#: Voices that need nothing from the network.
OFFLINE = ("kokoro", "windows")

#: name -> fn(text, voice, rate[, style]) -> (bytes, mime). Tests replace entries.
PROVIDERS: Dict[str, Callable[..., tuple]] = {}


class SpeechFailed(RuntimeError):
    pass


# ------------------------------------------------------------------ cues
SOUNDS = ("giggle", "laugh", "gasp", "sigh", "hmm", "yay", "pause")
SPANS = ("whisper", "soft", "excited", "slow", "emphasis", "tease")
_SOUND = re.compile(r"\[(" + "|".join(SOUNDS) + r")\]", re.I)
_SPAN = re.compile(r"\[(" + "|".join(SPANS) + r")\](.*?)\[/\1\]", re.I | re.S)
_ANY_CUE = re.compile(r"\[/?(?:" + "|".join(SOUNDS + SPANS) + r")\]", re.I)
#: How a sound is said by voices that can't make the sound itself.
INTERJECTIONS = {"giggle": "hehe", "laugh": "haha!", "gasp": "oh!", "sigh": "haah…", "hmm": "hmm…",
                 "yay": "yay!", "pause": ""}
#: How Nova Sonic is told to perform it.
DIRECTIONS = {"giggle": "(giggles)", "laugh": "(laughs)", "gasp": "(gasps)", "sigh": "(sighs)",
              "hmm": "(hmm)", "yay": "(cheers)", "pause": "…",
              "whisper": "whispering", "soft": "softly", "excited": "excitedly", "slow": "slowly",
              "emphasis": "with emphasis", "tease": "teasingly"}


def strip_cues(text: str) -> str:
    """Text without sound cues (for the chat)."""
    clean = _ANY_CUE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", clean).replace(" ,", ",").replace(" .", ".").strip()


def segments(text: str) -> List[Tuple[str, str, Tuple[str, ...]]]:
    """Text → [("text", words, styles) | ("sound", name, ())]. Spans don't nest."""
    out: List[Tuple[str, str, Tuple[str, ...]]] = []

    def add_plain(chunk: str, styles: Tuple[str, ...] = ()):
        position = 0
        for match in _SOUND.finditer(chunk):
            if match.start() > position:
                out.append(("text", chunk[position:match.start()], styles))
            out.append(("sound", match.group(1).lower(), ()))
            position = match.end()
        if position < len(chunk):
            out.append(("text", chunk[position:], styles))

    position = 0
    for match in _SPAN.finditer(text or ""):
        if match.start() > position:
            add_plain(text[position:match.start()])
        add_plain(match.group(2), (match.group(1).lower(),))
        position = match.end()
    add_plain((text or "")[position:])
    # Stray tags (an unclosed span) are dropped.
    return [(kind, _ANY_CUE.sub("", value) if kind == "text" else value, styles)
            for kind, value, styles in out if kind == "sound" or _ANY_CUE.sub("", value).strip()]


def add_cues(text: str, expressiveness: float = 0.5) -> str:
    """Gentle cues for a reply that has none: laughs, surprises, excitement."""
    if expressiveness <= 0.15 or _ANY_CUE.search(text or ""):
        return text
    text = re.sub(r"\b(ha(ha)+|he(he)+|lol)\b[!.,]*\s*", "[giggle] ", text, count=1, flags=re.I)
    text = re.sub(r"\b(oops|whoops|uh[- ]oh)\b", r"[gasp] \1", text, count=1, flags=re.I)
    if expressiveness >= 0.5:
        # One short exclamation, said with a lift.
        text = re.sub(r"(?<![\w\]])([^.!?\[\]]{3,60}!)", r"[excited]\1[/excited]", text, count=1)
    return text


def speakable(text: str, limit: int = MAX_SPOKEN_CHARS, expressiveness: float = 0.0) -> str:
    """A short, plain version of an answer, fit to be read aloud (cues kept)."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)          # [label](url) → label
    text = re.sub(r"\[(?:[A-Z]{1,3}\d+)\]", "", text)                   # citations [K1]
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if expressiveness:
        text = add_cues(text, expressiveness)
    if len(strip_cues(text)) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = ""
    for sentence in sentences:
        if len(strip_cues(spoken)) + len(strip_cues(sentence)) > limit:
            break
        spoken += (" " if spoken else "") + sentence
    if not spoken:
        spoken = strip_cues(text)[:limit].rsplit(" ", 1)[0] + "…"
    # A span cut in half would be dropped as a stray tag; close it.
    for name in SPANS:
        if spoken.lower().count(f"[{name}]") > spoken.lower().count(f"[/{name}]"):
            spoken += f"[/{name}]"
    return spoken + " The rest is in the chat."


def plain(text: str) -> str:
    """Cues said as words (for voices that only take text)."""
    parts = []
    for kind, value, _ in segments(text):
        parts.append(INTERJECTIONS.get(value, "") if kind == "sound" else value)
    return re.sub(r"\s{2,}", " ", " ".join(p for p in parts if p)).strip()


def directed(text: str) -> str:
    """Cues as stage directions (for Nova Sonic, which performs them)."""
    parts = []
    for kind, value, styles in segments(text):
        if kind == "sound":
            parts.append(DIRECTIONS[value])
        elif styles:
            parts.append(f"({DIRECTIONS[styles[0]]}:) {value.strip()}")
        else:
            parts.append(value)
    return re.sub(r"\s{2,}", " ", " ".join(p for p in parts if p)).strip()


# ------------------------------------------------------------ Polly SSML
#: SSML each Polly engine accepts (AWS Polly developer guide, "Supported SSML tags").
ENGINE_TAGS = {
    "standard": {"break", "emphasis", "prosody-rate", "prosody-volume", "prosody-pitch", "whisper",
                 "soft", "timbre", "breath", "drc", "news"},
    "neural": {"break", "prosody-rate", "prosody-volume", "drc", "news"},
    "long-form": {"break", "prosody-rate", "prosody-volume"},
    "generative": {"break", "prosody-rate", "prosody-volume"},
}
#: Voices with the newscaster style (neural only).
NEWS_VOICES = {"Matthew", "Joanna", "Amy", "Lupe"}


def _prosody(attrs: Dict[str, str], inner: str, allowed: set) -> str:
    keep = {k: v for k, v in attrs.items() if f"prosody-{k}" in allowed and v}
    if not keep:
        return inner
    return "<prosody " + " ".join(f'{k}="{v}"' for k, v in keep.items()) + f">{inner}</prosody>"


def render_ssml(text: str, engine: str = "neural", style: Dict[str, Any] = None, voice: str = "") -> str:
    """Spoken text (with cues) → SSML only this engine accepts."""
    style = style or {}
    allowed = ENGINE_TAGS.get(engine, ENGINE_TAGS["neural"])
    body = []
    for kind, value, styles in segments(text):
        if kind == "sound":
            word = INTERJECTIONS[value]
            if value == "pause":
                body.append('<break time="350ms"/>')
                continue
            breath = '<amazon:breath duration="x-short" volume="soft"/>' if "breath" in allowed \
                and value in ("sigh", "gasp") else ""
            lift = _prosody({"pitch": "+12%", "rate": "110%"}, escape(word, quote=False), allowed) \
                if value in ("giggle", "laugh", "yay") else escape(word, quote=False)
            body.append(f'{breath}{lift}<break time="150ms"/>')
            continue
        words = escape(value, quote=False)
        style_name = styles[0] if styles else ""
        if style_name == "whisper":
            words = f'<amazon:effect name="whispered">{words}</amazon:effect>' if "whisper" in allowed \
                else _prosody({"volume": "x-soft", "rate": "90%"}, words, allowed)
        elif style_name == "soft":
            words = f'<amazon:effect phonation="soft">{words}</amazon:effect>' if "soft" in allowed \
                else _prosody({"volume": "soft"}, words, allowed)
        elif style_name == "excited":
            words = _prosody({"rate": "110%", "volume": "loud", "pitch": "+10%"}, words, allowed)
        elif style_name == "slow":
            words = _prosody({"rate": "85%"}, words, allowed)
        elif style_name == "emphasis":
            words = f'<emphasis level="strong">{words}</emphasis>' if "emphasis" in allowed \
                else _prosody({"volume": "loud", "rate": "95%"}, words, allowed)
        elif style_name == "tease":
            words = _prosody({"rate": "92%", "pitch": "+15%"}, words, allowed)
            if "emphasis" in allowed:
                words = f'<emphasis level="moderate">{words}</emphasis>'
            words = f'<break time="120ms"/>{words}'
        body.append(words)
    inner = re.sub(r" {2,}", " ", " ".join(body)).strip()
    # The whole voice: speed, loudness, pitch, timbre, breaths, compression, news style.
    rate = float(style.get("rate") or 1.0)
    attrs = {"rate": f"{int(round(rate * 100))}%" if abs(rate - 1.0) > 0.01 else "",
             "volume": f"{float(style.get('volume') or 0):+.0f}dB" if style.get("volume") else "",
             "pitch": f"{float(style.get('pitch') or 0):+.0f}%" if style.get("pitch") else ""}
    inner = _prosody(attrs, inner, allowed)
    if style.get("timbre") and "timbre" in allowed:
        inner = f'<amazon:effect vocal-tract-length="{float(style["timbre"]):+.0f}%">{inner}</amazon:effect>'
    if style.get("breaths") and "breath" in allowed:
        inner = f'<amazon:auto-breaths volume="soft" frequency="low">{inner}</amazon:auto-breaths>'
    if style.get("drc") and "drc" in allowed:
        inner = f'<amazon:effect name="drc">{inner}</amazon:effect>'
    if style.get("newscaster") and "news" in allowed and engine == "neural" and voice in NEWS_VOICES:
        inner = f'<amazon:domain name="news">{inner}</amazon:domain>'
    return f"<speak>{inner}</speak>"


# ------------------------------------------------------------ choosing
def order(preferred: str = None) -> List[str]:
    chosen = (preferred or settings.VOICE_PROVIDER or "auto").lower()
    if chosen == "off":
        return []
    if chosen in ALL:
        # A chosen online voice falls back to the offline ones, never to another online one.
        return [chosen, *[p for p in OFFLINE if p != chosen]]
    return ["kokoro", "windows", "polly", "edge"]


def polly_ready() -> bool:
    mode = (settings.BEDROCK_AUTH_MODE or "default").lower()
    if mode == "api_key":
        return False
    if mode == "keys":
        return bool(settings.BEDROCK_AWS_ACCESS_KEY_ID and settings.BEDROCK_AWS_SECRET_ACCESS_KEY)
    if mode == "profile":
        return bool(settings.BEDROCK_AWS_PROFILE)
    return (settings.LLM_PROVIDER or "").lower() == "bedrock"


def _ready(name: str) -> bool:
    if name in _overridden:
        return True
    if name == "polly":
        return polly_ready()
    if name == "sonic":
        from app.services import sonic

        return sonic.available()[0]
    if name == "kokoro":
        from app.services import kokoro_tts

        return kokoro_tts.available()[0]
    if name == "windows":
        return sys.platform == "win32"
    return True


def synthesize(text: str, fmt: str = "mp3", voice: str = None, rate: float = None,
               style: Dict[str, Any] = None) -> Dict[str, Any]:
    """Speak ``text`` (cues allowed): ``{"audio", "mime", "provider"}``. Raises SpeechFailed.

    ``style`` is the agent's voice (see :func:`app.services.personality.voice_settings`);
    ``voice`` / ``rate`` are the older per-agent overrides and win over Settings.
    """
    text = (text or "").strip()
    if not text or not strip_cues(text):
        raise SpeechFailed("Nothing to say")
    style = dict(style or {})
    if voice:
        style["voice"] = voice
    rate = min(2.0, max(0.5, float(rate or style.get("rate") or settings.VOICE_RATE or 1.0)))
    style["rate"] = rate
    if not style.get("cues", True):
        text = strip_cues(text)
    problems = []
    for name in order(style.get("provider")):
        fn = PROVIDERS.get(name)
        if fn is None or not _ready(name):
            continue
        provider_is_chosen = (style.get("provider") or settings.VOICE_PROVIDER or "auto").lower() == name
        chosen = DEFAULTS[name]
        if name == "sonic":
            chosen = settings.VOICE_SONIC_VOICE or chosen
        elif provider_is_chosen and settings.VOICE_NAME:
            chosen = settings.VOICE_NAME
        if style.get("voice") and _fits(name, style["voice"]):
            chosen = style["voice"]
        try:
            if len(inspect.signature(fn).parameters) >= 4:
                audio, mime = fn(text, chosen, rate, style)
            else:
                audio, mime = fn(plain(text), chosen, rate)
        except Exception as exc:  # noqa: BLE001 - try the next voice
            problems.append(explain(name, exc))
            continue
        if audio:
            if fmt == "wav" and mime != "audio/wav":
                audio, mime = to_wav(audio), "audio/wav"
            return {"audio": audio, "mime": mime, "provider": name}
    raise SpeechFailed("No voice could speak: " + ("; ".join(problems) or "none available"))


_KOKORO_VOICE = re.compile(r"^[a-z]{2}_[a-z]+(?::[\d.]+)?(?:[+,][a-z]{2}_[a-z]+(?::[\d.]+)?)*$")


def _fits(provider: str, voice: str) -> bool:
    """Is ``voice`` a name this provider understands?

    Kokoro's look like af_heart (or a blend, af_heart+af_bella); Edge's like
    en-US-AriaNeural; Nova Sonic's are lower-case (tiffany); Polly's are
    capitalised (Ruth)."""
    edge_like = "-" in voice and voice.endswith("Neural")
    if provider == "kokoro":
        return bool(_KOKORO_VOICE.match(voice))
    if provider == "edge":
        return edge_like
    if provider == "sonic":
        return not edge_like and voice.islower() and "_" not in voice
    if provider == "polly":
        return not edge_like and voice[:1].isupper()
    return not edge_like


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
_POLLY_VOICES: Dict[str, Any] = {"at": 0.0, "voices": {}}
POLLY_ENGINES = ("generative", "neural", "long-form", "standard")


def polly_client():
    from app.services.llm_service import bedrock_session

    return bedrock_session().client("polly")


def polly_voices(client=None, fresh: bool = False) -> Dict[str, List[str]]:
    """Polly voice id → the engines it has (cached for a day)."""
    if _POLLY_VOICES["voices"] and not fresh and time.time() - _POLLY_VOICES["at"] < 86400:
        return _POLLY_VOICES["voices"]
    client = client or polly_client()
    voices: Dict[str, List[str]] = {}
    token = None
    while True:
        kwargs = {"NextToken": token} if token else {}
        page = client.describe_voices(**kwargs)
        for item in page.get("Voices", []):
            voices[item["Id"]] = list(item.get("SupportedEngines") or [])
        token = page.get("NextToken")
        if not token:
            break
    _POLLY_VOICES.update(at=time.time(), voices=voices)
    return voices


def engine_for(voice: str, wanted: str, voices: Dict[str, List[str]]) -> str:
    """The wanted engine if the voice has it, else the next one down."""
    have = voices.get(voice) or []
    if not have:
        return wanted
    start = POLLY_ENGINES.index(wanted) if wanted in POLLY_ENGINES else 1
    for engine in POLLY_ENGINES[start:] + POLLY_ENGINES[:start]:
        if engine in have:
            return engine
    return have[0]


def _polly(text: str, voice: str, rate: float, style: Dict[str, Any] = None) -> tuple:
    style = {**(style or {}), "rate": rate}
    client = polly_client()
    voice = voice or DEFAULTS["polly"]
    wanted = (style.get("engine") or settings.VOICE_POLLY_ENGINE or "generative").lower()
    try:
        engine = engine_for(voice, wanted, polly_voices(client))
    except Exception:  # noqa: BLE001 - the voice list is a nicety: try what was asked
        engine = wanted
    attempts = [render_ssml(text, engine, style, voice),
                render_ssml(strip_cues(text), engine, {"rate": rate}, voice)]
    last = None
    for ssml in attempts:
        try:
            response = client.synthesize_speech(Text=ssml, TextType="ssml", OutputFormat="mp3",
                                                VoiceId=voice, Engine=engine)
            return response["AudioStream"].read(), "audio/mpeg"
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "ssml" not in str(exc).lower():
                break
            logger.warn("voice", "Polly refused the SSML; trying plainer", {"engine": engine,
                                                                           "error": str(exc)[:200]})
    if last is not None and "ssml" in str(last).lower():
        response = client.synthesize_speech(Text=plain(text), TextType="text", OutputFormat="mp3",
                                            VoiceId=voice, Engine=engine)
        return response["AudioStream"].read(), "audio/mpeg"
    raise last


_HOSTS = {"polly": "Amazon Polly (polly.<region>.amazonaws.com)", "edge": "speech.platform.bing.com",
          "sonic": "Amazon Bedrock (bedrock-runtime)"}
_NETWORK_HINTS = ("cannot connect to host", "getaddrinfo", "name or service not known", "proxy", "403",
                  "timed out", "timeout", "connection", "ssl", "certificate", "unreachable", "refused",
                  "could not connect", "endpointconnectionerror", "nodename nor servname")


def explain(name: str, exc: Exception) -> str:
    """One provider's failure, in words a person can act on."""
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if name in _HOSTS and any(hint in lowered for hint in _NETWORK_HINTS):
        return (f"{name}: couldn't reach {_HOSTS[name]} — your network probably blocks it. "
                "The offline voice (Kokoro) works without the internet.")
    return f"{name}: {text[:160]}"


def _kokoro(text: str, voice: str, rate: float, style: Dict[str, Any] = None) -> tuple:
    from app.services import kokoro_tts

    return kokoro_tts.speak(text, voice=voice or None, style={**(style or {}), "rate": rate}), "audio/wav"


def _sonic(text: str, voice: str, rate: float, style: Dict[str, Any] = None) -> tuple:
    from app.services import sonic

    return sonic.speak(directed(text), voice=voice, style=style or {}), "audio/wav"


def _edge(text: str, voice: str, rate: float, style: Dict[str, Any] = None) -> tuple:
    import edge_tts

    style = style or {}
    percent = int(round((rate - 1.0) * 100))
    volume = int(round(float(style.get("volume") or 0) * 8))
    pitch = int(round(float(style.get("pitch") or 0) * 2))

    async def run() -> bytes:
        communicate = edge_tts.Communicate(plain(text), voice or DEFAULTS["edge"], rate=f"{percent:+d}%",
                                           volume=f"{volume:+d}%", pitch=f"{pitch:+d}Hz")
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
        return pool.submit(asyncio.run, coroutine).result(timeout=90)


def windows_ssml(text: str, style: Dict[str, Any] = None) -> str:
    """SSML 1.0 for the Windows voice: prosody, emphasis and breaks (no whisper)."""
    style = style or {}
    body = []
    for kind, value, styles in segments(text):
        if kind == "sound":
            body.append('<break time="300ms"/>' if value == "pause"
                        else f"{escape(INTERJECTIONS[value], quote=False)}<break time=\"150ms\"/>")
            continue
        words = escape(value, quote=False)
        name = styles[0] if styles else ""
        if name in ("whisper", "soft"):
            words = f'<prosody volume="x-soft">{words}</prosody>'
        elif name == "excited":
            words = f'<prosody rate="fast" pitch="high">{words}</prosody>'
        elif name == "slow":
            words = f'<prosody rate="slow">{words}</prosody>'
        elif name in ("emphasis", "tease"):
            words = f'<emphasis>{words}</emphasis>'
        body.append(words)
    pitch = float(style.get("pitch") or 0)
    inner = re.sub(r" {2,}", " ", " ".join(body)).strip()
    if pitch:
        inner = f'<prosody pitch="{pitch:+.0f}%">{inner}</prosody>'
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'{inner}</speak>')


def _windows(text: str, voice: str, rate: float, style: Dict[str, Any] = None) -> tuple:
    if sys.platform != "win32":
        raise SpeechFailed("The Windows voice is only on Windows")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "speech.wav"
        source = Path(folder) / "speech.xml"
        source.write_text(windows_ssml(text, style), encoding="utf-8")
        speed = int(round((rate - 1.0) * 10))
        pick = f"$s.SelectVoice('{voice}');" if voice else ""
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"{pick}$s.Rate={max(-10, min(10, speed))};"
                  f"$s.SetOutputToWaveFile('{out}');"
                  f"try {{ $s.SpeakSsml([IO.File]::ReadAllText('{source}')) }} catch {{ "
                  f"$s.Speak([IO.File]::ReadAllText('{source}') -replace '<[^>]+>', '') }};$s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not out.is_file():
            raise SpeechFailed("Windows didn't produce any speech")
        return out.read_bytes(), "audio/wav"


_BUILT_IN = {"kokoro": _kokoro, "polly": _polly, "sonic": _sonic, "edge": _edge, "windows": _windows}
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
    return pcm_to_wav(samples.tobytes(), 24000)


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    """Raw 16-bit mono PCM → a WAV file."""
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return out.getvalue()


def status() -> Dict[str, Any]:
    from importlib import util

    from app.services import sonic

    def has(module):
        try:
            return util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    from app.services import kokoro_tts

    sonic_ok, sonic_why = sonic.available()
    kokoro_ok, kokoro_why = kokoro_tts.available()
    return {"provider": (settings.VOICE_PROVIDER or "auto").lower(), "order": order(),
            "kokoro": kokoro_ok, "kokoro_detail": kokoro_why,
            "polly": polly_ready(), "polly_engine": settings.VOICE_POLLY_ENGINE,
            "sonic": sonic_ok, "sonic_detail": sonic_why, "sonic_voice": settings.VOICE_SONIC_VOICE,
            "edge": has("edge_tts"), "windows": sys.platform == "win32",
            "cues": bool(settings.VOICE_CUES), "live": sonic_ok}


def voices(provider: str) -> List[Dict[str, Any]]:
    """Voices to pick from, for the agent editor."""
    if provider == "kokoro":
        from app.services import kokoro_tts

        return kokoro_tts.voices()
    if provider == "polly":
        try:
            return [{"id": vid, "engines": engines} for vid, engines in sorted(polly_voices().items())]
        except Exception as exc:  # noqa: BLE001
            raise SpeechFailed(f"Couldn't list Polly voices: {exc}") from exc
    if provider == "sonic":
        from app.services import sonic

        return [{"id": v, "label": label} for v, label in sonic.VOICES.items()]
    if provider == "edge":
        return [{"id": v} for v in ("en-US-AriaNeural", "en-US-AnaNeural", "en-US-JennyNeural",
                                    "en-GB-SoniaNeural", "en-GB-MaisieNeural", "en-AU-NatashaNeural",
                                    "en-US-GuyNeural", "en-GB-RyanNeural")]
    return []
