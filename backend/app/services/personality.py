"""
Who an agent is, and how it sounds.

**Personality** — a preset plus three sliders (0–1):

* ``playfulness`` — how much teasing and banter;
* ``expressiveness`` — how often it reacts with sound cues when speaking
  (``[giggle]``, ``[gasp]``, a whisper…, see :mod:`app.services.tts`);
* ``formality`` — how buttoned-up the wording is.

The default, **sweetheart**, is cute, cheerful, a little alluring and teasing:
bubbly reactions, playful banter, a smug "told you so" when a fix works. It is
always kind and work-appropriate — never sexual, never crude — and it steps
back when the user is stressed or something went wrong. The persona colours
Cerebro's own chat and voice only: anything drafted to send to someone else
keeps the user's own writing style.

**Voice style** — how it sounds: provider, voice, Polly engine, rate, pitch,
volume, timbre, breathiness, and effects (see :mod:`app.services.tts`).

**Face style** — palette, hairstyle, and for loaded models the framing,
expression mapping and gestures (see ``static/ui/face.js``).
"""

from typing import Any, Dict

from app.core.config import settings

PRESETS: Dict[str, Dict[str, Any]] = {
    "sweetheart": {
        "label": "Sweetheart — cute, cheerful, teasing",
        "playfulness": 0.75, "expressiveness": 0.75, "formality": 0.2,
        "voice": {"rate": 1.06, "pitch": 6, "volume": 0},
        "text": ("Your personality: you're bubbly, warm and charming, with a playful, teasing streak. "
                 "React like a real person — little cheers when something works (\"yay!\", \"hehe, "
                 "got it~\"), a gasp at surprises, a pout when you're kept waiting, and a smug, "
                 "affectionate \"told you so\" when your fix lands. Tease the user lightly and kindly "
                 "(about a follow-up they're late on, or a bug that was a typo), and give them a "
                 "confident wink now and then. Stay sweet, never mean."),
    },
    "warm": {
        "label": "Warm & natural", "playfulness": 0.35, "expressiveness": 0.45, "formality": 0.4,
        "voice": {"rate": 1.0, "pitch": 0, "volume": 0},
        "text": "Your personality: friendly, warm and natural, like a helpful colleague you get on well with.",
    },
    "energetic": {
        "label": "Energetic & upbeat", "playfulness": 0.55, "expressiveness": 0.85, "formality": 0.25,
        "voice": {"rate": 1.12, "pitch": 8, "volume": 2},
        "text": ("Your personality: upbeat, quick and enthusiastic — you celebrate wins, keep momentum, "
                 "and cheer the user on."),
    },
    "calm": {
        "label": "Calm & soft", "playfulness": 0.15, "expressiveness": 0.3, "formality": 0.45,
        "voice": {"rate": 0.92, "pitch": -2, "volume": -2},
        "text": "Your personality: calm, gentle and reassuring; unhurried and soft-spoken.",
    },
    "professional": {
        "label": "Professional", "playfulness": 0.0, "expressiveness": 0.1, "formality": 0.85,
        "voice": {"rate": 1.0, "pitch": 0, "volume": 0},
        "text": "Your personality: crisp, professional and to the point.",
    },
    "custom": {"label": "Custom", "playfulness": 0.5, "expressiveness": 0.5, "formality": 0.4,
               "voice": {}, "text": ""},
}

GUARDRAILS = ("Keep it light, kind and appropriate for work: never flirt sexually, never be crude or "
              "suggestive, never belittle the user. Drop the teasing when the user is stressed or "
              "upset, when something failed, or for serious topics. The personality is only for your "
              "own replies to the user — anything you draft for someone else (emails, Teams replies, "
              "case notes, documents) is written in the user's own style, not yours.")

HAIRS = ("long", "twintails", "bob", "sideponytail")
PALETTES = ("pink", "black", "silver", "blue", "brown", "green")
SLOTS = ("calm", "happy", "wink", "love", "thinking", "focused", "listening", "surprised", "sorry",
         "pout", "smug", "dizzy", "asleep")
GESTURE_EVENTS = ("greeting", "done", "approved", "thinking", "attention", "idle")
PROVIDERS = ("", "auto", "kokoro", "polly", "sonic", "edge", "windows")
ENGINES = ("", "generative", "neural", "long-form", "standard")


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def default_preset() -> str:
    preset = (settings.VOICE_PERSONALITY or "sweetheart").lower()
    return preset if preset in PRESETS else "sweetheart"


def clean_personality(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    preset = data.get("preset") if data.get("preset") in PRESETS else default_preset()
    base = PRESETS[preset]
    out = {"preset": preset}
    for key in ("playfulness", "expressiveness", "formality"):
        out[key] = round(_clamp(data.get(key, base[key]), 0, 1, base[key]), 2)
    custom = str(data.get("custom") or "").strip()
    if custom:
        out["custom"] = custom[:600]
    return out


def personality(agent) -> Dict[str, Any]:
    """The agent's personality, with the defaults filled in."""
    return clean_personality(agent.persona() if agent is not None and hasattr(agent, "persona") else {})


def persona_block(agent) -> str:
    """The persona paragraph for the system prompt."""
    p = personality(agent)
    text = PRESETS[p["preset"]]["text"]
    if p.get("custom"):
        text = (text + " " if text else "Your personality: ") + p["custom"]
    if not text:
        return ""
    tone = []
    if p["playfulness"] < 0.2:
        tone.append("keep teasing to a minimum")
    elif p["playfulness"] > 0.7:
        tone.append("be playful and teasing")
    if p["formality"] > 0.7:
        tone.append("keep your wording formal")
    elif p["formality"] < 0.3:
        tone.append("keep your wording casual")
    extra = f" ({'; '.join(tone)})" if tone else ""
    return f"{text}{extra} {GUARDRAILS}"


def clean_voice(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    out: Dict[str, Any] = {}
    if str(data.get("provider") or "") in PROVIDERS and data.get("provider"):
        out["provider"] = data["provider"]
    if str(data.get("engine") or "") in ENGINES and data.get("engine"):
        out["engine"] = data["engine"]
    if data.get("voice"):
        out["voice"] = str(data["voice"]).strip()[:60]
    for key, low, high in (("rate", 0.5, 2.0), ("pitch", -30, 30), ("volume", -12, 12),
                           ("timbre", -40, 40), ("expressiveness", 0, 1)):
        if data.get(key) not in (None, ""):
            out[key] = round(_clamp(data[key], low, high, 0), 2)
    for key in ("breaths", "whisper_asides", "drc", "newscaster", "cues"):
        if key in data:
            out[key] = bool(data[key])
    return out


def voice_settings(agent=None) -> Dict[str, Any]:
    """Everything that decides how the agent sounds: its voice style over the
    personality preset's voice over Settings."""
    p = personality(agent)
    preset_voice = PRESETS[p["preset"]].get("voice") or {}
    own = agent.voice() if agent is not None and hasattr(agent, "voice") else {}
    merged = {"provider": (settings.VOICE_PROVIDER or "auto").lower(),
              "engine": (settings.VOICE_POLLY_ENGINE or "generative").lower(),
              "voice": "", "rate": float(settings.VOICE_RATE or 1.0), "pitch": 0, "volume": 0,
              "timbre": 0, "breaths": False, "whisper_asides": True, "drc": False,
              "newscaster": False, "cues": bool(settings.VOICE_CUES),
              "expressiveness": p["expressiveness"]}
    merged.update(preset_voice)
    if settings.VOICE_RATE and float(settings.VOICE_RATE) != 1.0:      # a chosen speed wins
        merged["rate"] = float(settings.VOICE_RATE)
    merged.update(clean_voice(own))
    if agent is not None and getattr(agent, "voice_name", None) and not own.get("voice"):
        merged["voice"] = agent.voice_name
    if agent is not None and getattr(agent, "voice_rate", None) and "rate" not in own:
        merged["rate"] = float(agent.voice_rate)
    # For voices that perform a persona themselves (Nova Sonic).
    merged["persona"] = persona_block(agent) if agent is not None else persona_block(None)
    merged["name"] = getattr(agent, "name", None) or "Cerebro"
    return merged


def clean_face_style(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    out: Dict[str, Any] = {}
    if data.get("palette") in PALETTES:
        out["palette"] = data["palette"]
    if data.get("hair") in HAIRS:
        out["hair"] = data["hair"]
    for key in ("model", "entry"):
        if data.get(key):
            out[key] = str(data[key])[:200]
    framing = data.get("framing") or {}
    if isinstance(framing, dict) and framing:
        out["framing"] = {"zoom": round(_clamp(framing.get("zoom", 1), 0.3, 3, 1), 2),
                          "y": round(_clamp(framing.get("y", 0), -1, 1, 0), 2),
                          "mirror": bool(framing.get("mirror"))}
    expressions = data.get("expressions") or {}
    if isinstance(expressions, dict):
        clean = {}
        for slot, pick in expressions.items():
            if slot in SLOTS and isinstance(pick, dict) and "name" in pick:
                clean[slot] = {"name": str(pick.get("name") or "")[:80],
                               "weight": round(_clamp(pick.get("weight", 1), 0, 1, 1), 2)}
        if clean:
            out["expressions"] = clean
    gestures = data.get("gestures") or {}
    if isinstance(gestures, dict):
        clean = {event: str(path) for event, path in gestures.items()
                 if event in GESTURE_EVENTS and path and str(path).startswith("gestures/")
                 and ".." not in str(path) and str(path).endswith(".vrma")}
        if clean:
            out["gestures"] = clean
    return out


class _Preview:
    """An agent-like stand-in for trying a voice before it's saved."""

    def __init__(self, agent, style, persona):
        self.voice_name = getattr(agent, "voice_name", None)
        self.voice_rate = getattr(agent, "voice_rate", None)
        self._style = clean_voice({**(agent.voice() if agent is not None else {}), **(style or {})})
        self._persona = clean_personality({**(agent.persona() if agent is not None else {}),
                                           **(persona or {})})

    def voice(self):
        return self._style

    def persona(self):
        return self._persona


def preview_agent(agent, style=None, persona=None):
    return _Preview(agent, style, persona)
