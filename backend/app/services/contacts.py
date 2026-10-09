"""
Finding the person a message is meant for.

"Message Anthony Oddo" must work without a full name or an email address.
Cerebro already knows who writes to the user — senders of mail, people in
Teams chats, chat names — so a partial name is matched against those: every
word typed must start a word of the candidate's name, in any order
("anthony", "oddo anth" and "Oddo, Anthony" all find Anthony Oddo).
"""

import re
from typing import Any, Dict, List, Optional

from app.models.enterprise import EnterpriseMessage

SCAN_LIMIT = 3000
_WORD = re.compile(r"[a-z0-9]+")


def words(name: str) -> List[str]:
    """Lower-case words of a name; the email-address part is ignored."""
    return _WORD.findall(re.sub(r"<[^>]*>|\S+@\S+", " ", (name or "").lower()))


def matches(query: str, candidate: str) -> bool:
    """Does every word of ``query`` start a word of ``candidate``?"""
    wanted, have = words(query), words(candidate)
    return bool(wanted) and all(any(h.startswith(w) for h in have) for w in wanted)


def same_name(query: str, candidate: str) -> bool:
    return sorted(words(query)) == sorted(words(candidate))


def known_people(db, teams_names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """People Cerebro has seen, merged by name: ``{name, email, chat}``."""
    people: Dict[str, Dict[str, Any]] = {}

    def person(name: str) -> Dict[str, Any]:
        key = " ".join(sorted(words(name)))
        return people.setdefault(key, {"name": name.strip(), "email": None, "chat": None})

    rows = (db.query(EnterpriseMessage.source, EnterpriseMessage.sender, EnterpriseMessage.sender_name,
                     EnterpriseMessage.thread_id, EnterpriseMessage.direct)
            .order_by(EnterpriseMessage.id.desc()).limit(SCAN_LIMIT))
    for source, sender, sender_name, thread_id, direct in rows:
        name = (sender_name or "").strip()
        if not name or "@" in name:
            continue
        found = person(name)
        if source == "outlook" and sender and "@" in sender:
            found["email"] = found["email"] or sender
        if source == "teams" and direct and thread_id:
            found["chat"] = found["chat"] or thread_id
    for chat_id, topic in (teams_names or {}).items():
        if topic and not topic.lower().startswith("chat with"):
            continue
        name = re.sub(r"^chat with\s+", "", topic or "", flags=re.IGNORECASE)
        if name:
            person(name)["chat"] = person(name)["chat"] or chat_id
    return list(people.values())


def resolve(db, name: str, teams_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Who is ``name``? ``{"status": "found", "person": {...}}``, ``"ambiguous"``
    with ``options``, or ``"none"``. A name that matches exactly wins over
    longer names that merely contain it.
    """
    found = [p for p in known_people(db, teams_names) if matches(name, p["name"])]
    exact = [p for p in found if same_name(name, p["name"])]
    found = exact or found
    if len(found) == 1:
        return {"status": "found", "person": found[0]}
    if found:
        return {"status": "ambiguous", "options": found[:6]}
    return {"status": "none"}
