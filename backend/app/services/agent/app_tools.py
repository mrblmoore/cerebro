"""
Doing things in Windows desktop apps: list windows, open, read, type, click
and press keys — the desktop twin of the browse tools.

Only programs the user named (registered under Add-ons → Desktop apps) are
driven. Reading, typing and ordinary clicks run straight away; a click on a
save/send/submit/delete/OK button, or Ctrl+S / Ctrl+Enter, is prepared as an
approval card with a screenshot of the window — unless the user switched
that app to apply changes automatically, when it's made and logged.
"""

from typing import Any, Dict, Optional

from app.core import logger
from app.services import desktop_apps
from app.services.agent import actions
from app.services.agent.registry import ToolContext, schema, string_param, tool

#: The window the last app tool worked in, so a job's next turn can carry on.
_last: Dict[str, Any] = {}


def _on(ctx: ToolContext) -> bool:
    return desktop_apps.available()


def _user_named(ctx: ToolContext, *values: str) -> bool:
    said = (ctx.context.get("_user_text") or "").lower()
    if not said:
        return False
    for value in values:
        value = (value or "").strip().lower()
        if not value or len(value) < 3:
            continue
        stem = value.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].replace(".exe", "")
        if value in said or (len(stem) >= 3 and stem in said):
            return True
    return False


def _remember(ctx: ToolContext, app: str, window: Dict[str, Any]) -> None:
    state = {"app": app, "handle": window["handle"], "title": window.get("title", "")}
    ctx.context["_app"] = state
    _last.clear()
    _last.update(state)


def _current(ctx: ToolContext) -> Optional[Dict[str, Any]]:
    return ctx.context.get("_app") or (dict(_last) if _last else None)


def _resolve(ctx: ToolContext, window: str = "") -> Dict[str, Any]:
    """The registered app and its window the call is about."""
    if window:
        name = desktop_apps.match(window)
        if name is None:
            for win in desktop_apps.backend().windows():
                if window.lower() in win["title"].lower():
                    name = desktop_apps.match(win["title"])
                    if name is None and _user_named(ctx, window):
                        name = desktop_apps.register(ctx.db, win["title"], window=window,
                                                     added_by="cerebro")["name"]
                    if name:
                        _remember(ctx, name, win)
                        return _current(ctx)
            raise desktop_apps.AppError(
                f"“{window}” isn't a desktop app Cerebro may use. Add it first with "
                "add_desktop_app (only programs the user names).")
        found = desktop_apps.find_window(name)
        if found is None:
            raise desktop_apps.AppError(f"{name} isn't open; use app_open first.")
        _remember(ctx, name, found)
        return _current(ctx)
    state = _current(ctx)
    if not state:
        raise desktop_apps.AppError("No app is open yet; use app_open first.")
    alive = {w["handle"] for w in desktop_apps.backend().windows()}
    if state["handle"] not in alive:
        found = desktop_apps.find_window(state["app"])
        if found is None:
            raise desktop_apps.AppError(f"{state['app']}'s window has closed; use app_open again.")
        _remember(ctx, state["app"], found)
        state = _current(ctx)
    return state


def _read(ctx: ToolContext, state: Dict[str, Any]) -> Dict[str, Any]:
    snap = desktop_apps.backend().snapshot(state["handle"])
    ctx.context.setdefault("_app_snaps", {})[state["handle"]] = snap
    return snap


def _fail(exc: Exception, summary: str) -> dict:
    return {"content": str(exc), "summary": summary}


# ------------------------------------------------------------------ tools
@tool("app_windows",
      "List the windows open on this PC, and the desktop apps Cerebro may use (added under "
      "Add-ons → Desktop apps).",
      schema([]), label="List windows", activity="browsing", available=_on)
def app_windows(ctx: ToolContext, **_) -> dict:
    try:
        windows = desktop_apps.backend().windows()
    except desktop_apps.AppError as exc:
        return _fail(exc, "Not available")
    registered = desktop_apps.definitions()
    lines = [f"- {w['title']}" + (f"  (app: {desktop_apps.match(w['title'])})"
                                  if desktop_apps.match(w["title"]) else "") for w in windows[:60]]
    apps = ", ".join(f"{d.get('label') or n}" for n, d in registered.items()) or "none yet"
    return {"content": f"Desktop apps Cerebro may use: {apps}.\nOpen windows:\n" + "\n".join(lines),
            "summary": f"{len(windows)} windows"}


@tool("app_open",
      "Start a Windows desktop app, or bring its window to the front, and read it. Give the "
      "program's name as the user said it (e.g. 'Notepad', 'Excel', 'our CRM'), its path, or its "
      "window title. A program the user named is added under Desktop apps on first use.",
      schema(["app"], app=string_param("The program's name, path or window title.")),
      label="Open an app", activity="writing", available=_on)
def app_open(ctx: ToolContext, app: str = "", **_) -> dict:
    try:
        name = desktop_apps.match(app)
        if name is None:
            if not _user_named(ctx, app):
                return {"content": f"“{app}” isn't a desktop app Cerebro may use, and the user didn't "
                                   "name it. Ask them, or have them add it under Add-ons.",
                        "summary": "Not added"}
            looks_like_path = "\\" in app or "/" in app or app.lower().endswith(".exe")
            name = desktop_apps.register(ctx.db, app.rsplit("\\", 1)[-1].replace(".exe", "")
                                         if looks_like_path else app,
                                         path=app if looks_like_path else app.lower(),
                                         window="" if looks_like_path else app,
                                         added_by="cerebro")["name"]
        window = desktop_apps.open_app(name)
        _remember(ctx, name, window)
        snap = _read(ctx, _current(ctx))
    except (desktop_apps.AppError, ValueError) as exc:
        return _fail(exc, "Couldn't open")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't open {app}: {exc}", "Couldn't open")
    return {"content": desktop_apps.describe(snap), "summary": snap["title"] or "Opened",
            "max_chars": 14_000}


@tool("app_read",
      "Read a desktop app's window: its text and a numbered list of buttons, fields, lists, tabs "
      "and menus to use with app_click and app_type. With no window, reads the one in use.",
      schema([], window=string_param("Part of the window title, or the app's name (optional).")),
      label="Read an app", activity="browsing", available=_on)
def app_read(ctx: ToolContext, window: str = "", **_) -> dict:
    try:
        snap = _read(ctx, _resolve(ctx, window))
    except desktop_apps.AppError as exc:
        return _fail(exc, "Couldn't read")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't read the window: {exc}", "Couldn't read")
    return {"content": desktop_apps.describe(snap), "summary": snap["title"] or "Read",
            "max_chars": 14_000}


@tool("app_type",
      "Type into a field in the app's window (by its number from app_read). Doesn't save or "
      "send anything.",
      schema(["ref", "text"], ref=string_param("The field's number."),
             text=string_param("What to type (replaces what's there).")),
      label="Type in an app", activity="writing", available=_on)
def app_type(ctx: ToolContext, ref: str = "", text: str = "", **_) -> dict:
    try:
        state = _resolve(ctx)
        snap = (ctx.context.get("_app_snaps") or {}).get(state["handle"]) or _read(ctx, state)
        control = next((c for c in snap["controls"] if c["ref"] == str(ref)), None)
        if control is None:
            return {"content": f"There's no item {ref}; read the window again.", "summary": "Not found"}
        desktop_apps.backend().type(state["handle"], str(ref), text)
    except desktop_apps.AppError as exc:
        return _fail(exc, "Couldn't type")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't type there: {exc}", "Couldn't type")
    fills = ctx.context.setdefault("_app_fills", {})
    fills[str(ref)] = {"name": control["name"], "id": control.get("id", ""),
                       "type": control["type"], "value": text}
    return {"content": f"Typed into [{ref}] {control['name'] or control['type']}. Nothing has been "
                       "saved or sent.", "summary": "Typed"}


def _preview(state, fills, then: str, image: Optional[str]) -> dict:
    preview = {"fields": [{"name": f["name"] or f["type"], "before": "", "after": f["value"]}
                          for f in fills] + [{"name": "Then", "before": "", "after": then}],
               "window": state.get("title")}
    if image:
        preview["image"] = image
    return preview


def _offer(ctx, state, args, title, then, why) -> dict:
    fills = list((ctx.context.get("_app_fills") or {}).values())
    args = {**args, "app": state["app"], "title": state.get("title", ""), "fills": fills}
    image = desktop_apps.screenshot_name(state["handle"])
    result = actions.offer(ctx, "app_action", f"app:{state['app']}", title, args,
                           preview=_preview(state, fills, then, image),
                           summary=why or f"In {state.get('title') or state['app']}")
    if result["summary"] == "Waiting for approval":
        result["content"] = (f"That would save, send or confirm something in {state['app']}, so it's "
                             "prepared as a change with a screenshot, waiting for the user's "
                             "approval. Tell them what it will do; nothing has happened yet.")
    return result


@tool("app_click",
      "Click a button, tab, menu item or list item in the app's window (by its number). Ordinary "
      "clicks happen straight away; one that saves, sends, submits, deletes or confirms is "
      "prepared for the user's approval with a screenshot.",
      schema(["ref"], ref=string_param("The item's number."),
             why=string_param("One line on what the click does, for the approval card.")),
      label="Click in an app", activity="writing", available=_on)
def app_click(ctx: ToolContext, ref: str = "", why: str = "", **_) -> dict:
    try:
        state = _resolve(ctx)
        snap = (ctx.context.get("_app_snaps") or {}).get(state["handle"]) or _read(ctx, state)
        control = next((c for c in snap["controls"] if c["ref"] == str(ref)), None)
        if control is None:
            return {"content": f"There's no item {ref}; read the window again.", "summary": "Not found"}
        if desktop_apps.is_risky(control["name"]):
            return _offer(ctx, state, {"click": {k: control.get(k, "") for k in ("name", "id", "type")}},
                          f"Click “{control['name']}” in {state['app']}",
                          f"click “{control['name']}”", why)
        desktop_apps.backend().click(state["handle"], str(ref))
        state = _resolve(ctx)
        after = _read(ctx, state)
    except desktop_apps.AppError as exc:
        return _fail(exc, "Couldn't click")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't click that: {exc}", "Couldn't click")
    return {"content": "Clicked.\n\n" + desktop_apps.describe(after), "summary": "Clicked",
            "max_chars": 14_000}


@tool("app_keys",
      "Press keys in the app's window, e.g. 'ctrl+n', 'tab', 'enter', 'alt+f'. Keys that save or "
      "send (Ctrl+S, Ctrl+Enter, Alt+S) are prepared for the user's approval.",
      schema(["keys"], keys=string_param("The keys, e.g. 'ctrl+s'."),
             why=string_param("One line on what the keys do, for the approval card.")),
      label="Press keys", activity="writing", available=_on)
def app_keys(ctx: ToolContext, keys: str = "", why: str = "", **_) -> dict:
    try:
        state = _resolve(ctx)
        if desktop_apps.risky_keys(keys):
            return _offer(ctx, state, {"keys": keys}, f"Press {keys} in {state['app']}",
                          f"press {keys}", why)
        desktop_apps.backend().keys(state["handle"], keys)
        after = _read(ctx, _resolve(ctx))
    except desktop_apps.AppError as exc:
        return _fail(exc, "Couldn't press")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't press {keys}: {exc}", "Couldn't press")
    return {"content": f"Pressed {keys}.\n\n" + desktop_apps.describe(after), "summary": "Pressed",
            "max_chars": 14_000}


@tool("add_desktop_app",
      "Let Cerebro use a Windows desktop program the user named: its name, its executable path "
      "(or command, e.g. 'notepad'), and/or its window title. It appears under Add-ons → Desktop "
      "apps, where the user can let its changes apply automatically.",
      schema(["label"], label=string_param("What to call it, e.g. 'Billing tool'."),
             path=string_param("The program's path or command (optional)."),
             window=string_param("Text in its window title (optional)."),
             description=string_param("What it's for (optional).")),
      mode="write", label="Add a desktop app", activity="writing")
def add_desktop_app(ctx: ToolContext, label: str = "", path: str = "", window: str = "",
                    description: str = "", **_) -> dict:
    if not _user_named(ctx, label, path, window):
        return {"content": "Only programs the user names can be added. Ask them which program.",
                "summary": "Not named"}
    try:
        data = desktop_apps.register(ctx.db, label, path=path, window=window, added_by="cerebro",
                                     description=description)
    except ValueError as exc:
        return _fail(exc, "Couldn't add")
    from app.services import capabilities

    ctx.drafts.append(capabilities.record_added(ctx.db, "app", data["name"], data["label"],
                                                path or window, "cerebro"))
    note = "" if desktop_apps.available() else \
        " (It can be driven only on Windows with desktop-app support installed.)"
    return {"content": f"Added {data['label']} as a desktop app; use app_open to start it.{note}",
            "summary": "Added"}


# ---------------------------------------------------------------- executor
def _find(snap: Dict[str, Any], want: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for control in snap["controls"]:
        if want.get("id") and control.get("id") == want["id"] and control["type"] == want.get("type"):
            return control
    for control in snap["controls"]:
        if control["name"] == want.get("name") and control["type"] == want.get("type"):
            return control
    return None


@actions.executor("app_action")
def _do_app_action(db, args: dict) -> dict:
    app = args["app"]
    back = desktop_apps.backend()
    window = desktop_apps.find_window(app)
    if window is None:
        raise desktop_apps.AppError(f"{app} isn't open any more; nothing was done.")
    handle = window["handle"]
    snap = back.snapshot(handle)
    for fill in args.get("fills") or []:
        control = _find(snap, fill)
        if control is not None and control.get("value") != fill["value"]:
            back.type(handle, control["ref"], fill["value"])
    if args.get("fills"):
        snap = back.snapshot(handle)
    before = (snap.get("title"), snap.get("text"),
              tuple((c["name"], c.get("value")) for c in snap["controls"]))
    try:
        if args.get("click"):
            control = _find(snap, args["click"])
            if control is None:
                raise desktop_apps.AppError(f"“{args['click'].get('name')}” isn't in the window any "
                                            "more; nothing was clicked.")
            back.click(handle, control["ref"])
            what = f"Clicked “{control['name']}”"
        else:
            back.keys(handle, args["keys"])
            what = f"Pressed {args['keys']}"
    except desktop_apps.AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - it may have gone through: never retried
        raise desktop_apps.AppError(f"It may or may not have gone through ({exc}). Check {app} "
                                    "before trying again.") from exc
    import time

    time.sleep(0.6)
    windows = back.windows()
    current = next((w for w in windows if w["handle"] == handle), None)
    after_snap = back.snapshot(handle) if current else None
    after = None if after_snap is None else (
        after_snap.get("title"), after_snap.get("text"),
        tuple((c["name"], c.get("value")) for c in after_snap["controls"]))
    changed = after != before
    dialogs = [w for w in windows if w.get("pid") == window.get("pid") and w["handle"] != handle]
    image = desktop_apps.screenshot_name(dialogs[0]["handle"] if dialogs else handle)
    out = {"detail": f"{what} in {app}", "after_image": image}
    if dialogs:      # a new dialog (Save As…) counts as a change too
        out["detail"] += f" — {dialogs[0]['title']} opened"
        changed = True
    if not changed:
        out["unconfirmed"] = True
        out["detail"] += " — but the window didn't change, so check it went through"
        try:
            from app.core import activity_state

            activity_state.notice(f"Check {app}", out["detail"], kind="change",
                                  link={"tab": "activity"})
        except Exception:  # noqa: BLE001
            pass
    logger.info("apps", "Desktop app change made", {"app": app})
    return out
