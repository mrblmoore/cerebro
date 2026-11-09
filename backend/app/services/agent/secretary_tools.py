"""
The secretary's tools: calendar and meetings, follow-ups and to-dos,
briefings, meeting notes, and documents from templates.

Reading happens straight away. Booking, moving and cancelling meetings, and
chasing someone, are prepared as cards for the user to approve; a document
from a template is a local change, approval-gated unless the user lets
document changes apply automatically.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.services import calendar, followups, secretary_docs
from app.services.agent import actions
from app.services.agent.registry import ToolContext, bool_param, schema, string_param, tool


def _cal_on(ctx: ToolContext) -> bool:
    return calendar.available()


def _int(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fail(exc: Exception) -> dict:
    return {"content": f"Couldn't reach the calendar: {exc}", "summary": "Calendar unavailable"}


# ---------------------------------------------------------------- calendar
@tool("calendar_today",
      "Your meetings for a day (default today) or the next few days, with times, places and "
      "who's coming.",
      schema([], day=string_param("'today', 'tomorrow', a weekday or a date (optional)."),
             days={"type": "integer", "description": "How many days from then (default 1)."}),
      label="Check the calendar", activity="searching", available=_cal_on)
def calendar_today(ctx: ToolContext, day: str = "", days: int = 1, **_) -> dict:
    start = calendar.parse_time(f"{day} 00:00") if day and day.lower() != "today" else None
    start = (start or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        found = calendar.events(start, start + timedelta(days=max(1, min(_int(days, 1), 14))))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    if not found:
        return {"content": f"No meetings from {start:%a %d %b}.", "summary": "No meetings"}
    lines = [f"- [{e['id'][:40]}] {calendar.describe(e)}" for e in found]
    return {"content": "Meetings (ids in brackets, for reschedule/cancel):\n" + "\n".join(lines),
            "summary": f"{len(found)} meeting(s)"}


@tool("find_free_time",
      "Find free time in your calendar during working hours on weekdays.",
      schema([], length_minutes={"type": "integer", "description": "How long (default 30)."},
             days={"type": "integer", "description": "How many days ahead to look (default 5)."},
             after=string_param("Not before this, e.g. 'tomorrow 10:00' (optional).")),
      label="Find free time", activity="searching", available=_cal_on)
def find_free_time(ctx: ToolContext, length_minutes: int = 30, days: int = 5, after: str = "", **_) -> dict:
    try:
        slots = calendar.free_slots(_int(length_minutes, 30), _int(days, 5),
                                    after=calendar.parse_time(after) if after else None)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    if not slots:
        return {"content": "No free time that long in working hours.", "summary": "Nothing free"}
    lines = [f"- {s['start']:%a %d %b %H:%M}–{s['end']:%H:%M}" for s in slots]
    return {"content": "Free (your calendar only; others' availability shows when the invite is "
                       "opened in Outlook):\n" + "\n".join(lines), "summary": f"{len(slots)} free slot(s)"}


def _attendees(ctx: ToolContext, text: str):
    from app.services import contacts

    emails, unknown = [], []
    for part in [p.strip() for p in (text or "").replace(";", ",").split(",") if p.strip()]:
        if "@" in part:
            emails.append(part)
            continue
        found = contacts.resolve(ctx.db, part)
        person = found.get("person") or {}
        address = person.get("email") or person.get("address")
        if found["status"] == "found" and address:
            emails.append(address)
        else:
            unknown.append(part)
    return emails, unknown


@tool("book_meeting",
      "Book a meeting (sends invitations when there are attendees). Prepared for the user's "
      "approval first. Use find_free_time to pick a time.",
      schema(["subject", "start"], subject=string_param("The meeting's title."),
             start=string_param("When, e.g. '2026-11-10 14:00' or 'tomorrow 2pm'."),
             length_minutes={"type": "integer", "description": "How long (default 30)."},
             attendees=string_param("Who to invite: names or emails, comma-separated (optional)."),
             body=string_param("Agenda or note (optional)."),
             location=string_param("Room or 'Teams' (optional).")),
      mode="approval", label="Book a meeting", activity="writing", available=_cal_on)
def book_meeting(ctx: ToolContext, subject: str = "", start: str = "", length_minutes: int = 30,
                 attendees: str = "", body: str = "", location: str = "", **_) -> dict:
    when = calendar.parse_time(start)
    if not subject.strip() or when is None:
        return {"content": "Give the meeting's title and when it starts.", "summary": "Needs more"}
    end = when + timedelta(minutes=max(5, _int(length_minutes, 30)))
    emails, unknown = _attendees(ctx, attendees)
    if unknown:
        return {"content": f"Cerebro doesn't know the email for {', '.join(unknown)}. Ask the user, or "
                           "use the address.", "summary": "Who's that?"}
    fields = [{"name": "When", "before": "", "after": f"{when:%a %d %b %H:%M}–{end:%H:%M}"},
              {"name": "Subject", "before": "", "after": subject}]
    if emails:
        fields.append({"name": "Invite", "before": "", "after": ", ".join(emails)})
    if location:
        fields.append({"name": "Where", "before": "", "after": location})
    return actions.offer(ctx, "book_meeting", "calendar", f"Book “{subject}”",
                         {"subject": subject, "start": when.isoformat(), "end": end.isoformat(),
                          "attendees": emails, "body": body, "location": location},
                         preview={"fields": fields}, summary=f"{when:%a %d %b %H:%M}")


@tool("reschedule_meeting",
      "Move a meeting to another time (prepared for approval). Name the meeting by its id from "
      "calendar_today or by its title.",
      schema(["meeting", "start"], meeting=string_param("The meeting's id or title."),
             start=string_param("The new start, e.g. 'friday 10:00'."),
             length_minutes={"type": "integer", "description": "New length (default: keep it)."}),
      mode="approval", label="Move a meeting", activity="writing", available=_cal_on)
def reschedule_meeting(ctx: ToolContext, meeting: str = "", start: str = "", length_minutes: int = 0, **_) -> dict:
    try:
        event = calendar.find(meeting)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    when = calendar.parse_time(start)
    if event is None or when is None:
        return {"content": "Say which meeting (see calendar_today) and the new time.", "summary": "Needs more"}
    length = _int(length_minutes, 0) or int((event["end"] - event["start"]).total_seconds() // 60)
    end = when + timedelta(minutes=length)
    return actions.offer(ctx, "reschedule_meeting", "calendar", f"Move “{event['subject']}”",
                         {"id": event["id"], "subject": event["subject"], "start": when.isoformat(),
                          "end": end.isoformat()},
                         preview={"fields": [{"name": "When", "before": f"{event['start']:%a %d %b %H:%M}",
                                              "after": f"{when:%a %d %b %H:%M}"}]},
                         summary=calendar.describe(event, with_people=False))


@tool("cancel_meeting", "Cancel a meeting, with an optional note to the attendees (prepared for approval).",
      schema(["meeting"], meeting=string_param("The meeting's id or title."),
             message=string_param("A short note to the attendees (optional).")),
      mode="approval", label="Cancel a meeting", activity="writing", available=_cal_on)
def cancel_meeting(ctx: ToolContext, meeting: str = "", message: str = "", **_) -> dict:
    try:
        event = calendar.find(meeting)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    if event is None:
        return {"content": "Say which meeting (see calendar_today).", "summary": "Needs more"}
    return actions.offer(ctx, "cancel_meeting", "calendar", f"Cancel “{event['subject']}”",
                         {"id": event["id"], "subject": event["subject"], "message": message},
                         preview={"fields": [{"name": calendar.describe(event), "before": "", "after": "Cancelled"}]
                                  + ([{"name": "Note", "before": "", "after": message}] if message else [])},
                         summary=calendar.describe(event, with_people=False))


@actions.executor("book_meeting")
def _do_book(db, args: dict) -> dict:
    return calendar.create(args["subject"], datetime.fromisoformat(args["start"]),
                           datetime.fromisoformat(args["end"]), args.get("attendees") or [],
                           args.get("body") or "", args.get("location") or "")


@actions.executor("reschedule_meeting")
def _do_move(db, args: dict) -> dict:
    return calendar.move(args["id"], datetime.fromisoformat(args["start"]), datetime.fromisoformat(args["end"]))


@actions.executor("cancel_meeting")
def _do_cancel(db, args: dict) -> dict:
    return calendar.cancel(args["id"], args.get("message") or "")


# ------------------------------------------------------- follow-ups, to-dos
@tool("todo_list",
      "Your to-do list: follow-ups you're waiting on, things you promised, things people asked "
      "you for, and to-dos — with when each is due.",
      schema([], include_done=bool_param("Include ones done in the last week.")), label="To-do list")
def todo_list(ctx: ToolContext, include_done: bool = False, **_) -> dict:
    from app.models.work import FollowUp

    items = followups.open_items(ctx.db)
    lines = [followups.describe(i) + (" — due to chase" if followups.chase_due(i) else "") for i in items]
    if include_done:
        recent = (ctx.db.query(FollowUp).filter(FollowUp.status == "done",
                                                 FollowUp.done_at >= datetime.now() - timedelta(days=7)).all())
        lines += [f"✓ {followups.describe(i)}" for i in recent]
    if not lines:
        return {"content": "Nothing on the list.", "summary": "Empty"}
    return {"content": "\n".join(lines), "summary": f"{len(items)} open"}


@tool("add_todo",
      "Add to the to-do list: something to do, something promised, or someone to wait on.",
      schema(["what"], what=string_param("What it is."),
             due=string_param("When it's due, e.g. 'friday', 'tomorrow' (optional)."),
             who=string_param("Who it's with (optional)."),
             kind=string_param("'todo' (default), 'promised' or 'waiting_on'.")),
      mode="write", label="Add a to-do", activity="writing")
def add_todo(ctx: ToolContext, what: str = "", due: str = "", who: str = "", kind: str = "todo", **_) -> dict:
    try:
        item = followups.add(ctx.db, what, kind or "todo", who=who or None,
                             due_at=followups.due_from(due) if due else None, source="chat",
                             agent_id=ctx.context.get("agent_id"),
                             conversation_id=ctx.context.get("conversation_id"))
    except ValueError as exc:
        return {"content": str(exc), "summary": "Needs more"}
    ctx.drafts.append({"type": "todo", "item": item.to_dict()})
    return {"content": f"Added {followups.describe(item)}.", "summary": "Added"}


@tool("done_todo", "Mark a to-do or follow-up done, or snooze it.",
      schema(["id"], id={"type": "integer", "description": "Its number from todo_list."},
             action=string_param("'done' (default), 'snooze' (a day) or 'reopen'.")),
      mode="write", label="Tick off a to-do", activity="writing")
def done_todo(ctx: ToolContext, id: int = 0, action: str = "done", **_) -> dict:  # noqa: A002
    from app.models.work import FollowUp

    item = ctx.db.get(FollowUp, _int(id, 0))
    if item is None:
        return {"content": "There's no such item; see todo_list.", "summary": "Not found"}
    result = followups.act(ctx.db, item, action or "done")
    return {"content": result.get("detail") or f"{followups.describe(item)} — {item.status}.",
            "summary": item.status.capitalize()}


@tool("chase_followup",
      "Draft a polite follow-up to someone who hasn't replied (by its number from todo_list), "
      "for the user to approve.",
      schema(["id"], id={"type": "integer", "description": "The follow-up's number."}),
      mode="approval", label="Chase a reply", activity="writing")
def chase_followup(ctx: ToolContext, id: int = 0, **_) -> dict:  # noqa: A002
    from app.models.work import FollowUp
    from app.services.agent.tools import _offer

    item = ctx.db.get(FollowUp, _int(id, 0))
    if item is None:
        return {"content": "There's no such item; see todo_list.", "summary": "Not found"}
    result = followups.chase(ctx.db, item)
    if not result["ok"]:
        return {"content": result["detail"], "summary": "Can't chase"}
    return {"content": _offer(ctx, result["action"]), "summary": "Waiting for approval"}


# ---------------------------------------------------------------- briefings
@tool("briefing",
      "Put together a briefing now: 'morning' (today's meetings, urgent mail, follow-ups due, cases, "
      "watches), 'evening' (what got done today and what's open) or 'weekly'.",
      schema([], kind=string_param("'morning' (default), 'evening' or 'weekly'.")),
      label="Brief me", activity="searching")
def briefing(ctx: ToolContext, kind: str = "morning", **_) -> dict:
    from app.services import briefings

    kind = kind if kind in ("morning", "evening", "weekly") else "morning"
    made = briefings.make(ctx.db, kind, force=True)
    return {"content": made.content, "summary": made.title, "max_chars": 8000}


# ---------------------------------------------------------- notes, documents
@tool("meeting_notes",
      "Take notes from a meeting: a summary, decisions and action items (added to the to-do list). "
      "The source can be 'last call' (a recording Cerebro transcribed), an attached or local "
      ".vtt/.docx/.txt transcript, a Teams recap link, or the transcript text itself.",
      schema(["source"], source=string_param("Where the transcript is.")),
      mode="write", label="Take meeting notes", activity="thinking")
def meeting_notes(ctx: ToolContext, source: str = "", **_) -> dict:
    try:
        made = secretary_docs.notes(ctx.db, source, agent_id=ctx.context.get("agent_id"),
                                    conversation_id=ctx.context.get("conversation_id"))
    except ValueError as exc:
        return {"content": str(exc), "summary": "No transcript"}
    except Exception as exc:  # noqa: BLE001
        return {"content": f"Couldn't read that: {exc}", "summary": "Couldn't read"}
    return {"content": made["text"] + "\n\n(To send a recap, prepare it with send_email — the user "
                                      "approves it.)",
            "summary": f"{len(made['followups'])} action item(s)", "max_chars": 10_000}


@tool("draft_document",
      "Make a Word or Excel document from a template, filling its {{placeholders}}. Call it with "
      "just the template first to see which placeholders it needs; then with data. Templates are "
      "found by name in the user's templates folder, or by path.",
      schema(["template"], template=string_param("The template's name or path."),
             data=string_param("JSON object of placeholder → value (omit to list the placeholders)."),
             save_to=string_param("Where to save it: a folder or file path (optional)."),
             why=string_param("One line on what the document is.")),
      mode="approval", label="Draft a document", activity="writing")
def draft_document(ctx: ToolContext, template: str = "", data: str = "", save_to: str = "", why: str = "",
                   **_) -> dict:
    path = secretary_docs.find_template(template)
    if path is None:
        known = secretary_docs.templates()
        return {"content": "No such template." + (f" Templates: {', '.join(known)}" if known else
                                                  " Set a templates folder in Settings → Secretary, or "
                                                  "give the template's path."), "summary": "Not found"}
    needed = secretary_docs.placeholders(path)
    try:
        values = json.loads(data) if isinstance(data, str) and data.strip() else (data or {})
    except ValueError:
        return {"content": "data must be a JSON object.", "summary": "Bad data"}
    if not values:
        return {"content": f"{path.name} needs: {', '.join(needed) or '(no placeholders)'}. Call again "
                           "with data.", "summary": f"{len(needed)} placeholder(s)"}
    missing = [n for n in needed if secretary_docs._value(values, n) is None]
    target = secretary_docs.target_for(path, save_to, values)
    fields = [{"name": n, "before": "", "after": str(secretary_docs._value(values, n) or "")[:300]}
              for n in needed if secretary_docs._value(values, n) is not None]
    fields.append({"name": "Save as", "before": "", "after": str(target)})
    result = actions.offer(ctx, "draft_document", "documents", f"Create {target.name}",
                           {"template": str(path), "data": values, "target": str(target)},
                           preview={"fields": fields[:25], "target": str(target)},
                           summary=why or f"From {path.name}")
    if missing:
        result["content"] += f" Left blank (not given): {', '.join(missing)}."
    return result


@actions.executor("draft_document")
def _do_draft(db, args: dict) -> dict:
    outcome = secretary_docs.fill(Path(args["template"]), args.get("data") or {}, Path(args["target"]))
    try:
        from app.services.document_service import DocumentService

        DocumentService(db).observe(outcome["path"], discovered_by="cerebro")
    except Exception:  # noqa: BLE001 - tracking is a nicety
        pass
    note = f" — still to fill: {', '.join(outcome['unfilled'])}" if outcome["unfilled"] else ""
    return {"detail": f"Created {Path(outcome['path']).name}{note}", "path": outcome["path"],
            "undo": {"path": outcome["path"], "backup": outcome.get("backup")}}


@actions.undoer("draft_document")
def _undo_draft(db, undo: dict) -> dict:
    import shutil

    path = Path(undo["path"])
    if undo.get("backup") and Path(undo["backup"]).exists():
        shutil.copy2(undo["backup"], path)
        return {"detail": f"Put {path.name} back as it was"}
    path.unlink(missing_ok=True)
    return {"detail": f"Removed {path.name}"}
