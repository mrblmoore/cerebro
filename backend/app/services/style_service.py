"""
Style learning and persona — making Cerebro write as you, and speak as you chose.

Two distinct things:

* **Persona** is how Cerebro addresses you and refers to itself — as a partner
  ("we should reply to Randy") or as an assistant ("shall I reply to Randy?").
  It shapes every sentence Cerebro says *to* you.
* **Voice** is how Cerebro writes *as* you — the diction, length and sign-off it
  uses when drafting a reply on your behalf. It is learned from things you have
  actually written: sent replies, your side of Teams threads, transcribed
  speech.

Voice is learned two ways, cheap first: measurable features (greeting, sign-off,
sentence length, formality) computed with no model, plus a handful of real
samples used as few-shot examples. When an LLM is available it also writes a
short prose "style card". All of it is folded into the drafting prompt.
"""

import json
import re
import statistics
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings

#: One row, well-known id, holds the learned voice. A tiny key/value would do,
#: but a table keeps it queryable and future-proof.
STYLE_SINGLETON_ID = 1

GREETINGS = ("hi", "hey", "hello", "good morning", "good afternoon", "thanks",
             "thank you", "team", "all")
SIGNOFFS = ("thanks", "thank you", "cheers", "best", "regards", "kind regards",
            "best regards", "talk soon", "many thanks", "br")


# --------------------------------------------------------------- persona
def persona() -> str:
    value = (settings.PERSONA or "assistant").lower()
    return value if value in ("assistant", "partner") else "assistant"


def persona_directive() -> str:
    """The instruction that makes Cerebro address the user in the chosen voice."""
    if persona() == "partner":
        return (
            "Speak as the user's second brain — first person plural, 'we' and "
            "'us', as if you and the user are one. E.g. \"we still owe Randy a "
            "reply\" or \"looks like we resolved this but never updated the case\"."
        )
    return (
        "Speak as the user's assistant — address them as 'you', refer to "
        "yourself lightly if at all. E.g. \"you still owe Randy a reply\" or "
        "\"want me to send that update?\"."
    )


# ---------------------------------------------------------- feature model
def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def analyse_sample(text: str) -> Dict[str, Any]:
    """Measurable features of one piece of writing — no model needed."""
    text = (text or "").strip()
    words = re.findall(r"[A-Za-z']+", text)
    sentences = _sentences(text)
    first_line = (text.splitlines() or [""])[0].strip().lower()
    last_lines = " ".join(text.splitlines()[-2:]).strip().lower()

    return {
        "words": len(words),
        "sentences": len(sentences),
        "avg_sentence_words": (len(words) / len(sentences)) if sentences else 0,
        "greeting": next((g for g in GREETINGS if first_line.startswith(g)), None),
        "signoff": next((s for s in SIGNOFFS if last_lines.startswith(s)
                         or last_lines.endswith(s)), None),
        "exclamations": text.count("!"),
        "contractions": len(re.findall(r"\b\w+'\w+\b", text)),
    }


def _aggregate(samples: List[str]) -> Dict[str, Any]:
    """Fold many samples into a voice profile."""
    analyses = [analyse_sample(s) for s in samples if s and len(s) > 20]
    if not analyses:
        return {}

    def common(key):
        values = [a[key] for a in analyses if a.get(key)]
        return Counter(values).most_common(1)[0][0] if values else None

    avg_len = statistics.mean(a["avg_sentence_words"] for a in analyses)
    exclaim_rate = statistics.mean(a["exclamations"] for a in analyses)
    contraction_rate = statistics.mean(
        a["contractions"] / max(a["words"], 1) for a in analyses)

    formality = "formal"
    if contraction_rate > 0.02 or exclaim_rate > 0.3:
        formality = "casual"
    elif contraction_rate > 0.005:
        formality = "neutral"

    return {
        "samples": len(analyses),
        "avg_sentence_words": round(avg_len, 1),
        "typical_greeting": common("greeting"),
        "typical_signoff": common("signoff"),
        "formality": formality,
        "uses_exclamations": exclaim_rate > 0.2,
    }


def describe_profile(profile: Dict[str, Any]) -> str:
    """A profile as plain guidance the LLM can follow."""
    if not profile:
        return ""
    parts = [f"Write in the user's voice ({profile.get('formality', 'neutral')})."]
    length = profile.get("avg_sentence_words")
    if length:
        parts.append(f"Sentences average about {length:.0f} words — "
                     f"{'keep them short' if length < 14 else 'full but not rambling'}.")
    if profile.get("typical_greeting"):
        parts.append(f"Usual greeting: \"{profile['typical_greeting'].title()}\".")
    if profile.get("typical_signoff"):
        parts.append(f"Usual sign-off: \"{profile['typical_signoff'].title()}\".")
    if not profile.get("uses_exclamations"):
        parts.append("Avoid exclamation marks.")
    return " ".join(parts)


# ------------------------------------------------------------- service
class StyleService:
    def __init__(self, db: Session):
        self.db = db

    def _row(self):
        from app.models.style import StyleProfile

        row = self.db.query(StyleProfile).get(STYLE_SINGLETON_ID)
        if row is None:
            row = StyleProfile(id=STYLE_SINGLETON_ID)
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    def add_sample(self, text: str, channel: str = "email") -> None:
        """Record a piece of the user's own writing to learn from."""
        text = (text or "").strip()
        if len(text) < 25 or not settings.STYLE_LEARNING_ENABLED:
            return

        from app.services.redaction import redact

        # Keep the shape of the writing, not its secrets.
        clean, _ = redact(text, redact_pii=True)

        row = self._row()
        samples = self._load(row.samples)
        samples.append({"text": clean[:2000], "channel": channel,
                        "at": datetime.utcnow().isoformat()})
        row.samples = json.dumps(samples[-40:])  # keep the most recent 40
        row.updated_at = datetime.utcnow()
        self.db.commit()

    def learn(self) -> Dict[str, Any]:
        """Recompute the voice profile from the collected samples."""
        row = self._row()
        samples = [s["text"] for s in self._load(row.samples)]
        if len(samples) < 3:
            return {"ok": False, "detail": f"Need a few writing samples first "
                                           f"(have {len(samples)})."}

        profile = _aggregate(samples)
        row.profile = json.dumps(profile)
        row.guidance = describe_profile(profile)

        # An LLM, if present, adds a richer prose description.
        from app.services.llm_service import LLMService

        llm = LLMService()
        if llm.enabled:
            joined = "\n---\n".join(samples[-8:])[:6000]
            card = llm._call_llm(
                "Describe this person's writing style in 2-3 sentences a "
                "ghostwriter could follow — tone, length, formality, quirks. "
                f"No preamble.\n\nSamples:\n{joined}")
            if card and "unavailable" not in card.lower():
                row.style_card = card.strip()

        row.updated_at = datetime.utcnow()
        self.db.commit()
        logger.info("style", "Learned writing voice", {"samples": len(samples)})
        return {"ok": True, "samples": len(samples), "profile": profile,
                "style_card": row.style_card}

    def drafting_directive(self) -> str:
        """Voice guidance plus a couple of real examples, for a drafting prompt."""
        if not settings.STYLE_LEARNING_ENABLED:
            return ""
        row = self._row()

        parts = []
        if row.style_card:
            parts.append(f"The user writes like this: {row.style_card}")
        elif row.guidance:
            parts.append(row.guidance)

        examples = [s["text"] for s in self._load(row.samples)][-2:]
        if examples:
            joined = "\n---\n".join(e[:600] for e in examples)
            parts.append(f"Examples of the user's own writing:\n{joined}")

        return "\n\n".join(parts)

    def status(self) -> Dict[str, Any]:
        row = self._row()
        samples = self._load(row.samples)
        return {
            "ok": True,
            "enabled": settings.STYLE_LEARNING_ENABLED,
            "samples": len(samples),
            "learned": bool(row.profile),
            "persona": persona(),
            "detail": (f"{len(samples)} sample(s) collected"
                       + (", voice learned" if row.profile else ", not yet learned")),
        }

    @staticmethod
    def _load(raw: Optional[str]) -> List[Dict[str, Any]]:
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []


def capture_user_writing(db: Session, text: str, channel: str = "email") -> None:
    """Convenience hook: called wherever the user's own words pass through."""
    if settings.STYLE_LEARNING_ENABLED:
        try:
            StyleService(db).add_sample(text, channel=channel)
        except Exception:
            pass
