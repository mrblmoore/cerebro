"""
Which Outlook and Teams messages Cerebro pays attention to.

Applied to every message the background monitor sees, before it is stored,
notified about or researched, so ads and chats the user doesn't care about
never reach Activity, notifications or Ask. All of it is set in Settings.

Teams (``TEAMS_WATCH``): everything · only 1:1 chats · 1:1 chats and
@mentions · only the chats and channels named in ``TEAMS_WATCH_CHATS`` (plus
@mentions). ``TEAMS_MUTE_CHATS`` is always ignored.

Outlook (``OUTLOOK_WATCH``): everything · only mail addressed to the user ·
only senders named in ``OUTLOOK_WATCH_SENDERS``. ``OUTLOOK_MUTE_SENDERS`` is
always ignored, and ``OUTLOOK_SKIP_BULK`` drops newsletters and promotions.

Anything that names a case is always kept: it is work, whatever sent it.
"""

import re
from typing import Any, Dict, List, Tuple

from app.core.config import settings

_BULK_SENDER = re.compile(r"(^|[._-])(no-?reply|do-?not-?reply|newsletters?|marketing|mailer|promo(tions?)?|"
                          r"deals|offers|news|updates)([._-]|@)", re.IGNORECASE)
_BULK_TEXT = re.compile(
    r"unsubscribe|view (this )?(email )?in (your )?browser|manage (your )?(email )?preferences|"
    r"email preferences|opt[- ]out|\d+% off|limited[- ]time|free shipping|special offer|"
    r"sale ends|shop now|buy now|webinar|register now", re.IGNORECASE)


def names(value: Any) -> List[str]:
    """A comma- or newline-separated setting as a list of lower-case terms."""
    return [part.strip().lower() for part in re.split(r"[,\n;]", str(value or "")) if part.strip()]


def _hits(terms: List[str], *fields: Any) -> bool:
    haystack = " ".join(str(f or "") for f in fields).lower()
    return any(term in haystack for term in terms)


def is_bulk(message: Dict[str, Any]) -> bool:
    sender = str(message.get("sender") or "")
    text = f"{message.get('subject') or ''}\n{message.get('body') or ''}"
    return bool(_BULK_SENDER.search(sender) or _BULK_TEXT.search(text))


def allow(source: str, message: Dict[str, Any]) -> Tuple[bool, str]:
    """May this message through? Returns ``(keep, reason_if_dropped)``."""
    from app.services.enterprise_service import detect_case

    if detect_case(message.get("subject") or "", message.get("body") or ""):
        return True, ""
    if source == "teams":
        return _allow_teams(message)
    if source == "outlook":
        return _allow_outlook(message)
    return True, ""


def _allow_teams(message: Dict[str, Any]) -> Tuple[bool, str]:
    where = (message.get("chat"), message.get("conversationId"), message.get("sender_name"))
    if _hits(names(settings.TEAMS_MUTE_CHATS), *where):
        return False, "muted chat"
    mode = settings.TEAMS_WATCH or "all"
    direct, mentioned = bool(message.get("direct")), bool(message.get("mentioned"))
    if mode == "direct" and not direct:
        return False, "not a 1:1 chat"
    if mode == "direct_mentions" and not (direct or mentioned):
        return False, "not a 1:1 chat or @mention"
    if mode == "selected" and not (mentioned or _hits(names(settings.TEAMS_WATCH_CHATS), *where)):
        return False, "not a chat you chose"
    return True, ""


def _allow_outlook(message: Dict[str, Any]) -> Tuple[bool, str]:
    who = (message.get("sender"), message.get("sender_name"))
    if _hits(names(settings.OUTLOOK_MUTE_SENDERS), *who, message.get("subject")):
        return False, "muted sender"
    mode = settings.OUTLOOK_WATCH or "all"
    if mode == "direct" and not message.get("direct"):
        return False, "not addressed to you"
    if mode == "selected" and not _hits(names(settings.OUTLOOK_WATCH_SENDERS), *who):
        return False, "not a sender you chose"
    if settings.OUTLOOK_SKIP_BULK and is_bulk(message):
        return False, "newsletter or promotion"
    return True, ""
