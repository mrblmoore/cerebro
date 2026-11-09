"""
Your calendar — Outlook on the web, through the hidden browser, signed in as you.

Reading uses the JSON Outlook's own calendar page fetches (OWA ``service.svc``
``GetCalendarView`` / ``FindItem`` and the REST/Graph ``calendarView`` shapes),
falling back to the events drawn on the page. Booking, moving and cancelling
go through Outlook's own event screens, the way a person would, and only
after the user approves the card.

The calendar source is swappable (``set_source``) so tests and other
calendars can stand in for Outlook. Events are cached for a few minutes so the
meeting-brief check every minute doesn't open Outlook every minute.
"""

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import logger

CACHE_SECONDS = 5 * 60
WORK_START, WORK_END = (9, 0), (17, 30)

_source = None
_cache: Dict[str, Any] = {}
_lock = threading.Lock()


class CalendarError(RuntimeError):
    pass


# ------------------------------------------------------------------ source
def set_source(source) -> None:
    """Use another calendar (tests); ``None`` goes back to Outlook."""
    global _source
    _source = source
    _cache.clear()


def source():
    if _source is not None:
        return _source
    from app.core.config import settings

    if not (settings.BROWSER_AUTOMATION_ENABLED and settings.OUTLOOK_BROWSER_ENABLED):
        raise CalendarError("Turn on Outlook (Connect → Outlook) so Cerebro can read your calendar.")
    from app.services.browser import outlook

    return outlook.connector


def available() -> bool:
    try:
        source()
        return True
    except CalendarError:
        return False


# ------------------------------------------------------------------ shapes
def _when(value: Any) -> Optional[datetime]:
    """OWA "2026-11-09T09:00:00+00:00", Graph {dateTime, timeZone}, or a datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone().replace(tzinfo=None)
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("DateTime")
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _person(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {"name": str(value or ""), "email": ""}
    box = value.get("Mailbox") or value.get("emailAddress") or value.get("EmailAddress") or value
    if not isinstance(box, dict):
        return {"name": value.get("Name") or "", "email": box if isinstance(box, str) else ""}
    return {"name": box.get("Name") or box.get("name") or "",
            "email": box.get("EmailAddress") or box.get("address") or box.get("Address") or ""}


def event_from(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A calendar event if ``node`` is shaped like one (OWA or REST/Graph)."""
    if not isinstance(node, dict):
        return None
    start, end = _when(node.get("Start") or node.get("start")), _when(node.get("End") or node.get("end"))
    subject = node.get("Subject") if "Subject" in node else node.get("subject")
    item = node.get("ItemId") or node.get("Id") or node.get("id")
    item = item.get("Id") if isinstance(item, dict) else item
    if not (start and end and item and subject is not None):
        return None
    location = node.get("Location") or node.get("location") or {}
    location = (location.get("DisplayName") or location.get("displayName") or "") \
        if isinstance(location, dict) else str(location or "")
    attendees = []
    for key in ("RequiredAttendees", "OptionalAttendees", "attendees"):
        for entry in node.get(key) or []:
            person = _person(entry)
            if person["name"] or person["email"]:
                attendees.append(person)
    show_as = str(node.get("FreeBusyType") or node.get("LegacyFreeBusyStatus")
                  or node.get("showAs") or "busy").lower()
    body = node.get("Body") or node.get("body") or {}
    body = (body.get("Value") or body.get("content") or "") if isinstance(body, dict) else str(body or "")
    if "<" in body:
        from app.services.browser.messaging import strip_html

        body = strip_html(body)
    online = node.get("onlineMeeting")
    online_url = node.get("OnlineMeetingJoinUrl") or (online.get("joinUrl") if isinstance(online, dict)
                                                       else None)
    return {"id": str(item), "subject": subject or "(no title)", "start": start, "end": end,
            "location": location, "organizer": _person(node.get("Organizer") or node.get("organizer")),
            "attendees": attendees, "all_day": bool(node.get("IsAllDayEvent") or node.get("isAllDay")),
            "cancelled": bool(node.get("IsCancelled") or node.get("isCancelled")),
            "show_as": show_as, "body": body[:2000],
            "online_url": online_url}


def plain(event: Dict[str, Any]) -> Dict[str, Any]:
    """An event as JSON."""
    return {**event, "start": event["start"].isoformat(), "end": event["end"].isoformat()}


# ------------------------------------------------------------------ reading
def events(start: datetime, end: datetime, fresh: bool = False) -> List[Dict[str, Any]]:
    """Events overlapping ``start``–``end``, earliest first (cancelled ones left out)."""
    key = f"{start:%Y%m%d%H%M}-{end:%Y%m%d%H%M}"
    with _lock:
        cached = _cache.get(key)
        if cached and not fresh and time.time() - cached[0] < CACHE_SECONDS:
            return [dict(e) for e in cached[1]]
    found = source().calendar(start, end)
    clean, seen = [], set()
    for event in found:
        if event["id"] in seen or event.get("cancelled"):
            continue
        if event["end"] <= start or event["start"] >= end:
            continue
        seen.add(event["id"])
        clean.append(event)
    clean.sort(key=lambda e: e["start"])
    with _lock:
        _cache[key] = (time.time(), clean)
    return [dict(e) for e in clean]


def today(day: datetime = None) -> List[Dict[str, Any]]:
    day = (day or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    return events(day, day + timedelta(days=1))


def forget() -> None:
    with _lock:
        _cache.clear()


def free_slots(length_minutes: int = 30, days: int = 5, after: datetime = None,
               work_start=WORK_START, work_end=WORK_END, limit: int = 8) -> List[Dict[str, datetime]]:
    """Gaps of at least ``length_minutes`` in working hours, on weekdays."""
    now = after or datetime.now()
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    busy = [e for e in events(start_day, start_day + timedelta(days=days + 1))
            if e.get("show_as") not in ("free", "0", "workingelsewhere") and not e.get("all_day")]
    slots = []
    for offset in range(days + 1):
        day = start_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        cursor = max(day.replace(hour=work_start[0], minute=work_start[1]), now)
        # Round up to the next quarter hour.
        if cursor.minute % 15 or cursor.second:
            cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=15 - cursor.minute % 15)
        close = day.replace(hour=work_end[0], minute=work_end[1])
        for event in [e for e in busy if e["start"].date() == day.date() or e["end"].date() == day.date()] + \
                [{"start": close, "end": close}]:
            if event["start"] - cursor >= timedelta(minutes=length_minutes) and cursor < close:
                slots.append({"start": cursor, "end": min(event["start"], close)})
                if len(slots) >= limit:
                    return slots
            cursor = max(cursor, event["end"])
    return slots


def find(text: str, days: int = 14) -> Optional[Dict[str, Any]]:
    """The event ``text`` names (its id, or words of its title), soonest first."""
    now = datetime.now() - timedelta(hours=2)
    found = events(now, now + timedelta(days=days))
    for event in found:
        if event["id"] == text:
            return event
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower())) - {"the", "a", "meeting", "with", "my"}
    best, score = None, 0
    for event in found:
        hay = (event["subject"] + " " + " ".join(a["name"] for a in event["attendees"])).lower()
        hit = sum(1 for w in words if w in hay)
        if hit > score:
            best, score = event, hit
    return best


def parse_time(text: str, base: datetime = None) -> Optional[datetime]:
    """'2026-11-10 14:00', '14:00', 'tomorrow 9:30', 'friday 3pm' → a datetime."""
    if isinstance(text, datetime):
        return text
    raw = (text or "").strip().lower()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("z", "").replace(" ", "T"))
    except ValueError:
        pass
    now = base or datetime.now()
    day = now
    if "tomorrow" in raw:
        day = now + timedelta(days=1)
    else:
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for index, name in enumerate(names):
            if name in raw or re.search(rf"\b{name[:3]}\b", raw):
                ahead = (index - now.weekday()) % 7 or 7
                day = now + timedelta(days=ahead)
                break
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", raw.replace("tomorrow", ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if match.group(3) == "pm" and hour < 12:
        hour += 12
    if match.group(3) == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def describe(event: Dict[str, Any], with_people: bool = True) -> str:
    when = "all day" if event.get("all_day") else f"{event['start']:%H:%M}–{event['end']:%H:%M}"
    people = ", ".join(a["name"] or a["email"] for a in event["attendees"][:8])
    where = f" @ {event['location']}" if event.get("location") else ""
    return f"{event['start']:%a %d %b} {when} {event['subject']}{where}" + \
        (f" — with {people}" if with_people and people else "")


# ------------------------------------------------------------------ changing
def create(subject: str, start: datetime, end: datetime, attendees: List[str] = None,
           body: str = "", location: str = "") -> Dict[str, Any]:
    result = source().create_event(subject, start, end, attendees or [], body, location)
    forget()
    logger.info("calendar", "Event booked", {"subject": subject[:60]})
    return result


def move(event_id: str, start: datetime, end: datetime) -> Dict[str, Any]:
    result = source().move_event(event_id, start, end)
    forget()
    return result


def cancel(event_id: str, message: str = "") -> Dict[str, Any]:
    result = source().cancel_event(event_id, message)
    forget()
    return result
