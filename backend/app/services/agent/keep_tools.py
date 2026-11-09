"""
"Keep it updated" in one step, and editing Word/Excel files.

``keep_updated`` connects whatever the user pointed at (a SharePoint page or
document, a website, a local or synced file, a desktop program, an add-on),
makes the job, watches the source when updates follow it, and posts the
"Keeping X updated" card. ``edit_document`` prepares a change to a Word or
Excel file — approval-gated unless the user lets document changes apply
automatically — with a backup and Undo.
"""

import json
from pathlib import Path

from app.services import keeping
from app.services.agent import actions
from app.services.agent.registry import ToolContext, schema, string_param, tool


def _said(ctx: ToolContext, *values: str) -> bool:
    said = (ctx.context.get("_user_text") or "").lower()
    if not said:
        return True          # a job run: the job itself was the user's request
    return any(v and v.strip().lower()[:60] in said for v in values)


@tool("keep_updated",
      "Keep a page, document, spreadsheet or app screen up to date — in one step. Use it for any "
      "'keep … updated', 'start updating …', 'every day update …' request. It connects the target "
      "if needed (adds the website, asks the user to sign in once, registers the program), makes "
      "the job, watches the source when updates should follow it, and runs the first update now. "
      "Don't ask for anything the link or file already tells you.",
      schema(["target", "instructions"],
             target=string_param("What to keep updated: a link (SharePoint, internal site, wiki), a "
                                 "file path (Word/Excel, local or synced), a desktop program's name, "
                                 "or an add-on's name."),
             instructions=string_param("What to change each time, in the user's words."),
             source=string_param("Where the updates come from (a link or file), if any."),
             when=string_param("How often: 'hourly', 'daily at 9', 'weekdays at 17:00', 'weekly', or "
                               "'when the source changes'. Default: daily, or on change with a source.")),
      mode="write", label="Keep it updated", activity="writing")
def keep_updated(ctx: ToolContext, target: str = "", instructions: str = "", source: str = "",
                 when: str = "", **_) -> dict:
    if not target.strip() or not instructions.strip():
        return {"content": "Say what to keep updated and what to change.", "summary": "Needs more"}
    if ctx.context.get("task_run"):
        return {"content": "This job already keeps it updated; do the update now instead.",
                "summary": "Already a job"}
    outcome = keeping.start(ctx.db, target, instructions, source=source, when=when,
                            agent_id=ctx.context.get("agent_id"),
                            conversation_id=ctx.context.get("conversation_id"),
                            user_named=_said(ctx, target))
    ctx.drafts.extend(outcome.get("cards") or [])
    if not outcome["ok"]:
        return {"content": outcome["note"], "summary": "Not connected yet"}
    task = outcome["task"]
    from app.services import jobs

    jobs.start_now(task.id)
    note = f" {outcome['note']}" if outcome.get("note") else ""
    source_note = (f" It watches {source} and runs when it changes." if outcome.get("watch") else "")
    return {"content": f"Done: “{task.title}” (job #{task.id}) runs {keeping.describe_schedule(task)}."
                       f"{source_note}{note} The first update is starting now; changes it prepares "
                       "wait for the user's approval unless they let this target update by itself. "
                       "Tell the user briefly; don't do the update yourself in this turn.",
            "summary": "Keeping it updated"}


@tool("list_updates", "List what you're keeping updated, how often, and when each last ran.",
      schema([]), label="What's kept updated")
def list_updates(ctx: ToolContext, **_) -> dict:
    items = keeping.listing(ctx.db)
    if not items:
        return {"content": "Nothing is being kept updated yet.", "summary": "None"}
    lines = [f"- #{i['task_id']} {i['label']} — {i['when']}, {i['status']}"
             f"{', updates by itself' if i['auto_apply'] else ''}" for i in items]
    return {"content": "\n".join(lines), "summary": f"{len(items)} kept updated"}


@tool("edit_document",
      "Change a Word (.docx) or Excel (.xlsx) file on this PC or a synced OneDrive/SharePoint "
      "folder. Give operations as JSON — Word: replace_text {find, replace}, append_paragraph "
      "{text}, insert_paragraph {index, text}, set_paragraph {index, text}, append_heading {text, "
      "level}; Excel: set_cell {sheet, cell, value}, append_row {sheet, values}, set_range {sheet, "
      "start, rows}. Read the file with read_document first. A backup is kept and it can be undone.",
      schema(["path", "operations"], path=string_param("The file's full path."),
             operations=string_param("A JSON list of operations."),
             why=string_param("One line on what the change does.")),
      mode="approval", label="Prepare a document change", activity="writing")
def edit_document(ctx: ToolContext, path: str = "", operations: str = "", why: str = "", **_) -> dict:
    from app.services import document_editors
    from app.services.document_readers import DocumentError

    target = Path((path or "").strip().strip('"')).expanduser()
    try:
        ops = json.loads(operations) if isinstance(operations, str) else operations
        if isinstance(ops, dict):
            ops = ops.get("operations") or [ops]
    except ValueError:
        return {"content": "operations must be a JSON list.", "summary": "Bad operations"}
    try:
        checked = document_editors.apply(target, ops, dry_run=True)
    except DocumentError as exc:
        return {"content": f"That change can't be made: {exc}", "summary": "Can't change"}
    fields = [{"name": (done.get("op") or "change").replace("_", " "), "before": "",
               "after": done.get("detail") or json.dumps(asked, default=str)[:300]}
              for asked, done in zip(ops, checked["operations"])]
    return actions.offer(ctx, "edit_document", "documents", f"Update {target.name}",
                         {"path": str(target), "operations": ops},
                         preview={"fields": fields[:20], "target": str(target)},
                         summary=why or f"{len(ops)} change(s)")


@actions.executor("edit_document")
def _do_edit(db, args: dict) -> dict:
    from app.services import document_editors

    outcome = document_editors.apply(Path(args["path"]), args["operations"])
    return {"detail": f"Updated {Path(args['path']).name} ({len(outcome['operations'])} change(s))",
            "undo": {"path": args["path"], "backup": outcome.get("backup")}}


@actions.undoer("edit_document")
def _undo_edit(db, undo: dict) -> dict:
    import shutil

    if not undo.get("backup") or not Path(undo["backup"]).exists():
        raise RuntimeError("There's no backup to put back.")
    shutil.copy2(undo["backup"], undo["path"])
    return {"detail": f"Put {Path(undo['path']).name} back as it was"}
