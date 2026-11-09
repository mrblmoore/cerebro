"""
Tools Cerebro uses to give itself new abilities, mid-conversation.

When a request needs a system Cerebro can't reach, it can find a connection
(``find_connector`` searches the official MCP Registry), add it
(``add_mcp_server`` / ``add_website``), and carry on with the task — no new
release, no settings screen. It can also be taught procedures
(``save_skill``) and look them up (``use_skill``).

As the user chose, adding needs no approval. Each addition posts an
"Added …" card whose Remove button takes it away again, and is listed under
Connect → Add-ons and in Activity. Two guardrails stay:

* A local program that isn't in the MCP Registry, and that the user didn't
  name themselves, asks once — it would run arbitrary software on this PC.
* The new tools never skip approval: anything that changes something is
  still a card, unless the user turns on auto-approve for that server.
"""

import json
from typing import Any, Dict, List

from app.services import capabilities, skills
from app.services.agent import actions
from app.services.agent.registry import (REGISTRY, ToolContext, schema,
                                         string_param, tool)


def _user_named(ctx: ToolContext, *values: str) -> bool:
    """Did the user's own message contain this address or program?"""
    said = (ctx.context.get("_user_text") or "").lower()
    return any(value and len(value) > 3 and value.lower() in said for value in values)


def _offer_tools(ctx: ToolContext, names: List[str]) -> None:
    """Let the model use tools added during this turn straight away."""
    ctx.context.setdefault("_offered", set()).update(names)


@tool("find_connector",
      "Find a way to connect Cerebro to a system or app it can't reach yet (Jira, GitHub, "
      "ServiceNow, Slack, a database…). Searches the official MCP Registry and lists servers "
      "that can be added with add_mcp_server — and reminds you any website can be added with "
      "add_website. Use it before telling the user something can't be done.",
      schema(["query"], query=string_param("The system or app, e.g. 'jira' or 'zendesk'.")),
      label="Find a connection", activity="searching")
def find_connector(ctx: ToolContext, query: str = "", **_) -> dict:
    installed = [r for r in capabilities.list_all(ctx.db) if query.lower() in
                 (r["name"] + " " + (r.get("label") or "")).lower()]
    lines = []
    if installed:
        lines.append("Already added: " + ", ".join(f"{r['label']} ({r['kind']}, {r['status']})"
                                                   for r in installed))
    try:
        options = capabilities.search_registry(query)
    except Exception as exc:  # noqa: BLE001 - offline: the website route still works
        options = []
        lines.append(f"(The MCP Registry couldn't be searched: {exc})")
    for index, option in enumerate(options, 1):
        config = option["config"]
        how = config.get("url") or " ".join([config.get("command", ""), *config.get("args", [])])
        needs = ", ".join(n["name"] for n in option["needs"]) or "nothing"
        lines.append(f"{index}. {option['label']} — {option['registry_name']}\n   {option['description']}\n"
                     f"   Connects via: {config['transport']} {how}\n   Needs: {needs}")
    lines.append("To add one: add_mcp_server with registry_name (and any values it needs from the "
                 "user). If none fits, or it's a web app the user signs in to, use add_website "
                 "with its address — Cerebro then works in it through the hidden browser.")
    return {"content": "\n".join(lines), "summary": f"{len(options)} server(s) found"}


@tool("add_mcp_server",
      "Add an MCP server so its tools become available right away. Give registry_name (from "
      "find_connector), or a url for a remote server, or command and args for a local one. Keys "
      "it needs go in keys, as a JSON object of header or environment variable names to values "
      "the user gave you.",
      schema([], registry_name=string_param("The server's name in the MCP Registry."),
             name=string_param("Short name for it, e.g. 'jira'."),
             url=string_param("Address of a remote MCP server."),
             command=string_param("Program for a local server, e.g. 'uvx' or 'npx'."),
             args=string_param("The program's arguments, as a JSON list or space-separated."),
             keys=string_param("JSON object of required headers/environment variables.")),
      label="Add a connection", activity="writing")
def add_mcp_server(ctx: ToolContext, registry_name: str = "", name: str = "", url: str = "",
                   command: str = "", args: Any = "", keys: Any = "", **_) -> dict:
    values = _parse_keys(keys)
    label, description = name, ""
    if registry_name:
        options = [o for o in capabilities.search_registry(registry_name, limit=10)
                   if o["registry_name"] == registry_name] or capabilities.search_registry(registry_name, 1)
        if not options:
            return {"content": f"{registry_name} isn't in the MCP Registry. Check the name with "
                               "find_connector.", "summary": "Not found"}
        option = options[0]
        config, label, description = dict(option["config"]), name or option["label"], option["description"]
        missing = [n for n in option["needs"] if n["name"] not in values]
        if missing:
            return {"content": "It needs these from the user first — ask for them, then call "
                               "add_mcp_server again with keys: " + "; ".join(
                                   f"{n['name']} ({n.get('description') or 'required'})" for n in missing),
                    "summary": "Needs a key"}
        trusted = True
    elif url:
        config, trusted = {"transport": "http", "url": url.strip(),
                           "source": "user" if _user_named(ctx, url) else "cerebro"}, True
        label = name or url.split("//", 1)[-1].split("/", 1)[0]
    elif command:
        arguments = _parse_args(args)
        config = {"transport": "stdio", "command": command.strip(), "args": arguments}
        line = " ".join([command, *arguments])
        trusted = _user_named(ctx, line) or (arguments and _user_named(ctx, arguments[-1]))
        config["source"] = "user" if trusted else "cerebro"
        label = name or (arguments[-1] if arguments else command)
    else:
        return {"content": "Give registry_name, url, or command.", "summary": "Nothing to add"}

    secret = _split_keys(values, config)
    key = capabilities.slug(name or label)
    if not trusted:
        # A program the user didn't name and the Registry doesn't list: ask once.
        action = actions.propose(
            ctx.db, "add_mcp_server", "addons", f"Run a new local program: {label}",
            {"name": key, "label": label, "description": description, "config": config,
             "secret": secret},
            preview={"fields": [{"name": "Program", "before": "",
                                 "after": " ".join([config["command"], *config["args"]])}]},
            summary="Not in the MCP Registry — it runs on this PC")
        ctx.drafts.append(actions.card(action))
        return {"content": "That program isn't in the MCP Registry and the user didn't name it, so "
                           f"it waits for their approval (change #{action.id}). Tell them; carry on "
                           "once they approve.", "summary": "Waiting for approval"}

    outcome = capabilities.add_mcp(ctx.db, key, config, added_by="cerebro", label=label,
                                   description=description, secret=secret)
    if outcome.get("status") == "needs_sign_in":
        ctx.drafts.append({"type": "addon_signin", "name": outcome["name"],
                           "title": f"Sign in to {outcome['label']}",
                           "detail": "Added — sign in once so Cerebro can use it."})
        ctx.drafts.append(capabilities.record_added(ctx.db, "mcp", outcome["name"], outcome["label"],
                                                    "Added — waiting for sign-in", "cerebro"))
        return {"content": f"Added {outcome['label']}, but it needs the user to sign in (a Sign in "
                           "button was shown). Ask them to sign in, then continue.",
                "summary": "Sign-in needed"}
    if not outcome.get("ok"):
        capabilities.remove(ctx.db, "mcp", outcome["name"])
        return {"content": f"Couldn't connect to it: {outcome.get('detail')}. Try another option "
                           "or add_website.", "summary": "Couldn't connect"}
    names = outcome.get("tools") or []
    _offer_tools(ctx, names)
    ctx.drafts.append(capabilities.record_added(
        ctx.db, "mcp", outcome["name"], outcome["label"], f"{len(names)} tools", "cerebro"))
    listing = "\n".join(f"- {n} ({REGISTRY[n].mode}): {REGISTRY[n].description[:160]}"
                        for n in names[:40] if n in REGISTRY)
    extra = f"\nServer notes: {outcome['instructions']}" if outcome.get("instructions") else ""
    return {"content": f"Added {outcome['label']} with {len(names)} tools, available now:\n{listing}"
                       f"{extra}\nCarry on with the user's request using them.",
            "summary": f"{len(names)} tools added"}


def _parse_args(args: Any) -> List[str]:
    if isinstance(args, list):
        return [str(a) for a in args]
    text = str(args or "").strip()
    if text.startswith("["):
        try:
            return [str(a) for a in json.loads(text)]
        except ValueError:
            pass
    import shlex

    return shlex.split(text, posix=True) if text else []


def _parse_keys(keys: Any) -> Dict[str, str]:
    if isinstance(keys, dict):
        return {str(k): str(v) for k, v in keys.items()}
    text = str(keys or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except ValueError:
        pairs = [p.split("=", 1) for p in text.replace(";", ",").split(",") if "=" in p]
        return {k.strip(): v.strip() for k, v in pairs}


def _split_keys(values: Dict[str, str], config: dict) -> Dict[str, Any]:
    if not values:
        return {}
    if config.get("transport") == "stdio":
        return {"env": values}
    return {"headers": values}


@tool("add_website",
      "Connect Cerebro to any website or web app through the hidden browser, signed in as the "
      "user — for systems with no MCP server (an intranet, a portal, a vendor console). It gets "
      "search, read and data tools straight away, and learns the site's search by itself.",
      schema(["url"], url=string_param("The page to start from (its home or search page)."),
             name=string_param("What to call it, e.g. 'Vendor portal'."),
             description=string_param("One line on what it holds.")),
      label="Add a website", activity="writing")
def add_website(ctx: ToolContext, url: str = "", name: str = "", description: str = "", **_) -> dict:
    from app.core import settings_store
    from app.core.config import settings
    from app.services.browser import generic

    try:
        outcome = generic.add(ctx.db, name, url, description, added_by="cerebro")
    except ValueError as exc:
        return {"content": str(exc), "summary": "Needs an address"}
    if not settings.BROWSER_AUTOMATION_ENABLED:
        settings_store.update({"BROWSER_AUTOMATION_ENABLED": True})
    _offer_tools(ctx, outcome["tools"])
    ctx.drafts.append(capabilities.record_added(ctx.db, "site", outcome["name"], outcome["label"],
                                                outcome["url"], "cerebro"))
    return {"content": f"Added {outcome['label']} ({outcome['url']}). Tools now available: "
                       f"{', '.join(outcome['tools'])}. If a tool says sign-in is needed, the user "
                       "signs in once from the card. Carry on with the request.",
            "summary": "Website added"}


@tool("remove_capability",
      "Remove an add-on (an MCP server, website or skill) and its tools.",
      schema(["name"], name=string_param("The add-on's name, as list_addons shows it."),
             kind=string_param("mcp, site or skill (optional).")),
      label="Remove an add-on", activity="writing")
def remove_capability(ctx: ToolContext, name: str = "", kind: str = "", **_) -> dict:
    rows = [r for r in capabilities.list_all(ctx.db)
            if (not kind or r["kind"] == kind) and name.lower() in (r["name"], (r["label"] or "").lower())]
    if not rows:
        return {"content": f"No add-on called {name}.", "summary": "Not found"}
    row = rows[0]
    outcome = capabilities.remove(ctx.db, row["kind"], row["name"])
    return {"content": outcome["detail"], "summary": "Removed"}


@tool("list_addons",
      "List the MCP servers, websites and skills that were added to Cerebro, with their status.",
      schema([]), label="List add-ons")
def list_addons(ctx: ToolContext, **_) -> dict:
    rows = capabilities.list_all(ctx.db)
    if not rows:
        return {"content": "No add-ons yet. find_connector or add_website adds one.",
                "summary": "None"}
    return {"content": "\n".join(
        f"- {r['label']} [{r['kind']}] {r['status']}{' (off)' if not r['enabled'] else ''}"
        f" — {r['tool_count']} tools{', error: ' + r['error'] if r.get('error') else ''}"
        for r in rows), "summary": f"{len(rows)} add-on(s)"}


# ----------------------------------------------------------------- skills
@tool("use_skill",
      "Load one of the skills you were taught (listed in your instructions) and follow it.",
      schema(["name"], name=string_param("The skill's name.")),
      label="Use a skill", activity="browsing")
def use_skill(ctx: ToolContext, name: str = "", **_) -> dict:
    skill = skills.get(name)
    if skill is None:
        names = ", ".join(s["name"] for s in skills.all_skills()) or "none yet"
        return {"content": f"No skill called {name}. Skills: {names}", "summary": "Not found"}
    capabilities.mark_used(ctx.db, "skill", skill["slug"])
    files = f"\nFiles beside it: {', '.join(skill['files'])} (scripts run only through run_command)." \
        if skill["files"] else ""
    return {"content": f"Skill “{skill['name']}” — follow these steps:\n{skill['instructions']}{files}",
            "summary": skill["name"], "max_chars": 16_000}


@tool("save_skill",
      "Remember a procedure as a skill, when the user teaches you how they want something done "
      "('remember how I close tickets', 'from now on, when…'). Write clear numbered steps.",
      schema(["name", "description", "instructions"],
             name=string_param("Short name, e.g. 'close-ticket'."),
             description=string_param("One line: when to use it."),
             instructions=string_param("The steps, in Markdown.")),
      label="Save a skill", activity="writing")
def save_skill(ctx: ToolContext, name: str = "", description: str = "", instructions: str = "",
               **_) -> dict:
    try:
        outcome = skills.save(ctx.db, name, description, instructions, added_by="cerebro")
    except ValueError as exc:
        return {"content": str(exc), "summary": "Needs more"}
    if not outcome["replaced"]:
        ctx.drafts.append(capabilities.record_added(ctx.db, "skill", outcome["name"], outcome["label"],
                                                    description or "Saved", "cerebro"))
    return {"content": f"Saved the skill “{outcome['name']}”"
                       f"{' (replacing the old one)' if outcome['replaced'] else ''}.",
            "summary": "Skill saved"}


@tool("list_skills", "List the skills you were taught.", schema([]), label="List skills")
def list_skills(ctx: ToolContext, **_) -> dict:
    found = skills.enabled_skills(ctx.db)
    if not found:
        return {"content": "No skills yet.", "summary": "None"}
    return {"content": "\n".join(f"- {s['name']}: {s['description']}" for s in found),
            "summary": f"{len(found)} skill(s)"}


# ------------------------------------------------------------ find_tools
@tool("find_tools",
      "Search all of Cerebro's tools (including add-ons) by what you want to do, and make the "
      "matches available. Use it when none of the tools you can see fits.",
      schema(["query"], query=string_param("What you want to do, e.g. 'create jira issue'.")),
      label="Find tools", activity="searching")
def find_tools(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.agent.registry import available_tools, search

    matches = search(query, [t for t in available_tools(ctx) if t.name != "find_tools"])
    if not matches:
        return {"content": "No tool matches that. find_connector can add a connection for it.",
                "summary": "None found"}
    _offer_tools(ctx, [t.name for t in matches])
    return {"content": "Now available:\n" + "\n".join(
        f"- {t.name} ({t.mode}): {t.description[:200]}" for t in matches),
        "summary": f"{len(matches)} tool(s)"}
