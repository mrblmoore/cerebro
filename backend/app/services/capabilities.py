"""
Capabilities added at runtime: MCP servers, websites and skills.

This is what lets Cerebro connect to something new without a new release.
Each add-on is a small file under ``CAPABILITIES_DIR``:

* ``mcp/<name>.json``      — an MCP server: a web address, or a program to run;
* ``sites/<name>.json``    — a website worked through the hidden browser;
* ``skills/<name>/SKILL.md`` — a procedure Cerebro was taught (Agent Skills format).

Its state — on or off, working or not, who added it — is a
:class:`~app.models.capability.Capability` row. Keys and sign-in tokens are
never in those files: they are kept in ``secrets.bin``, encrypted for the
Windows user (DPAPI).

Tools appear and disappear as add-ons are added and removed
(:func:`app.services.agent.registry.register_dynamic`), with no restart.

Safety, as in OpenMuse: a server's tools that only read (``readOnlyHint``)
run straight away; any other tool is prepared as an approval card, unless the
user turned on auto-approve for that server. A local program that isn't in
the MCP Registry and that the user didn't name asks once before it runs.
"""

import json
import re
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.paths import CAPABILITIES_DIR

KINDS = ("mcp", "site", "skill", "app")
REGISTRY_URL = "https://registry.modelcontextprotocol.io"
EXPORT_FORMAT = "cerebro-addons/1"

_lock = threading.RLock()
_connections: Dict[str, Any] = {}
_sign_in: Dict[str, Dict[str, Any]] = {}
_needs: Dict[str, str] = {}          # server -> resource metadata URL for sign-in
_started = False


# ------------------------------------------------------------------ files
def folder(kind: str):
    return CAPABILITIES_DIR / {"mcp": "mcp", "site": "sites", "skill": "skills", "app": "apps"}[kind]


def slug(text: str, limit: int = 32) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (value or "addon")[:limit].strip("-")


def tool_prefix(name: str) -> str:
    return name.replace("-", "_")


def _read_json(path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def mcp_config(name: str) -> Optional[dict]:
    return _read_json(folder("mcp") / f"{name}.json")


def save_mcp_config(name: str, config: dict) -> None:
    _write_json(folder("mcp") / f"{name}.json", config)


# ---------------------------------------------------------------- secrets
def _secrets_file():
    return CAPABILITIES_DIR / "secrets.bin"


def _load_secrets() -> Dict[str, Any]:
    from app.services.browser.engine import _unprotect

    try:
        return json.loads(_unprotect(_secrets_file().read_bytes()).decode("utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - unreadable: start over, but say so
        logger.warn("addons", "Saved add-on keys couldn't be read", {"error": str(exc)[:200]})
        return {}


def get_secret(name: str) -> Dict[str, Any]:
    with _lock:
        return dict(_load_secrets().get(name) or {})


def set_secret(name: str, value: Optional[Dict[str, Any]]) -> None:
    from app.services.browser.engine import _protect

    with _lock:
        data = _load_secrets()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        CAPABILITIES_DIR.mkdir(parents=True, exist_ok=True)
        _secrets_file().write_bytes(_protect(json.dumps(data).encode("utf-8")))


# -------------------------------------------------------------------- rows
def _row(db, kind: str, name: str):
    from app.models.capability import Capability

    return db.query(Capability).filter(Capability.kind == kind, Capability.name == name).first()


def _upsert(db, kind: str, name: str, **fields):
    from app.models.capability import Capability

    row = _row(db, kind, name)
    if row is None:
        row = Capability(kind=kind, name=name)
        db.add(row)
    for key, value in fields.items():
        if key == "tools":
            value = json.dumps(value)
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def list_all(db) -> List[dict]:
    from app.models.capability import Capability

    rows = db.query(Capability).order_by(Capability.kind, Capability.name).all()
    out = []
    for row in rows:
        item = row.to_dict()
        if row.kind == "mcp":
            config = mcp_config(row.name) or {}
            item["transport"] = config.get("transport") or ("stdio" if config.get("command") else "http")
            item["url"] = config.get("url")
            item["command"] = " ".join([config.get("command") or "", *(config.get("args") or [])]).strip() or None
            item["source"] = config.get("source")
            item["sign_in"] = dict(_sign_in.get(row.name) or {})
            item["has_key"] = bool(get_secret(row.name).get("headers") or get_secret(row.name).get("env"))
        elif row.kind == "app":
            from app.services import desktop_apps

            data = desktop_apps.definition(row.name) or {}
            item["path"] = data.get("path") or None
            item["window"] = data.get("window") or None
            item["auto_apply"] = bool(data.get("auto_apply"))
            item["available"] = desktop_apps.available()
        elif row.kind == "site":
            from app.services.browser import generic

            item["auto_apply"] = bool((generic.definition(row.name) or {}).get("auto_apply"))
        out.append(item)
    return out


def mark_used(db, kind: str, name: str) -> None:
    row = _row(db, kind, name)
    if row is not None:
        row.last_used_at = datetime.utcnow()
        db.commit()


# ============================================================ MCP servers
def _headers_for(name: str):
    def headers() -> Dict[str, str]:
        secret = get_secret(name)
        out = dict(secret.get("headers") or {})
        oauth = secret.get("oauth")
        if oauth and oauth.get("access_token"):
            if oauth.get("expires_at") and oauth["expires_at"] - 60 < time.time():
                from app.services import mcp_client

                fresh = mcp_client.refresh(oauth)
                if fresh:
                    set_secret(name, {**secret, "oauth": fresh})
                    oauth = fresh
            out["Authorization"] = f"Bearer {oauth['access_token']}"
        return out
    return headers


def connection(name: str):
    """The live connection to server ``name`` (made on first use)."""
    from app.services import mcp_client

    with _lock:
        conn = _connections.get(name)
        if conn is None:
            config = mcp_config(name)
            if config is None:
                raise mcp_client.MCPError(f"There's no add-on called {name}")
            conn = mcp_client.MCPConnection(
                name, config, headers=_headers_for(name), env=get_secret(name).get("env") or {},
                on_tools_changed=lambda: refresh_mcp(None, name))
            _connections[name] = conn
    return conn


def _drop_connection(name: str) -> None:
    with _lock:
        conn = _connections.pop(name, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _mode_for(definition: dict) -> str:
    hints = definition.get("annotations") or {}
    return "read" if hints.get("readOnlyHint") is True else "approval"


def _schema(definition: dict) -> dict:
    schema = definition.get("inputSchema") or {}
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    schema = {k: v for k, v in schema.items() if k not in ("$schema",)}
    schema.setdefault("properties", {})
    return schema


def register_mcp_tools(name: str, definitions: List[dict], label: str = "") -> List[str]:
    """Make a server's tools Ask tools named ``<server>__<tool>``."""
    from app.services.agent.registry import Tool, register_dynamic, safe_name, unregister_owner

    owner = f"mcp:{name}"
    unregister_owner(owner)
    names = []
    for definition in definitions:
        tool_name = definition.get("name")
        if not tool_name:
            continue
        mode = _mode_for(definition)
        hints = definition.get("annotations") or {}
        title = hints.get("title") or definition.get("title") or tool_name.replace("_", " ")
        description = (definition.get("description") or title).strip()
        description = f"[{label or name}] {description}"[:1500]
        if mode == "approval":
            description += " (Changes something: the user approves it first unless auto-approve is on.)"
        item = Tool(
            name=safe_name(f"{tool_prefix(name)}__{tool_name}"), description=description,
            handler=_mcp_handler(name, tool_name, mode, f"{label or name}: {title}"),
            parameters=_schema(definition), mode=mode,
            label=f"{label or name}: {title}"[:60],
            activity="searching" if mode == "read" else "writing",
            keywords=f"{name} {label} {tool_name.replace('_', ' ')}")
        names.append(register_dynamic(item, owner).name)
    return names


def _mcp_handler(server: str, tool_name: str, mode: str, title: str):
    def handler(ctx, **arguments) -> dict:
        from app.services import mcp_client
        from app.services.agent import actions

        mark_used(ctx.db, "mcp", server)
        if mode == "read":
            try:
                result = connection(server).call_tool(tool_name, arguments)
            except mcp_client.NeedsSignIn:
                _set_status(ctx.db, server, "needs_sign_in", "Sign in to use it")
                ctx.drafts.append({"type": "addon_signin", "name": server,
                                   "title": f"Sign in to {server}",
                                   "detail": "Then ask again."})
                return {"content": f"{server} needs the user to sign in first (a Sign in button "
                                   "was shown). Tell the user.", "summary": "Sign-in needed"}
            text = mcp_client.result_text(result)
            if result.get("isError"):
                return {"content": f"{tool_name} reported an error: {text}", "summary": "Error"}
            return {"content": text, "summary": f"{len(text)} characters",
                    "max_chars": 12_000}
        row = _row(ctx.db, "mcp", server)
        preview = [{"name": key, "before": "", "after": value if isinstance(value, str)
                    else json.dumps(value, indent=1, default=str)}
                   for key, value in (arguments or {}).items()]
        action = actions.propose(ctx.db, "mcp_call", f"mcp:{server}", title,
                                 {"server": server, "tool": tool_name, "arguments": arguments or {}},
                                 preview={"fields": preview[:20]},
                                 summary=f"{server} → {tool_name}")
        if row is not None and row.auto_approve:
            actions.approve(ctx.db, action.id, automatic=True)
            ctx.db.refresh(action)
            ctx.drafts.append(actions.card(action))
            if action.status == "done":
                output = action.result_dict().get("output") or action.result_dict().get("detail")
                return {"content": f"Done automatically (auto-approve is on). Result:\n{output}",
                        "summary": "Done"}
            return {"content": f"It failed: {action.error}. Don't retry by yourself — tell the user.",
                    "summary": "Failed"}
        ctx.drafts.append(actions.card(action))
        return {"content": f"Prepared as change #{action.id}; it runs only when the user approves "
                           "it. Say what it will do; never claim it is done.",
                "summary": "Waiting for approval"}
    return handler


def _set_status(db, name: str, status: str, error: str = None) -> None:
    row = _row(db, "mcp", name)
    if row is not None:
        row.status, row.error = status, error
        db.commit()


def refresh_mcp(db, name: str) -> Dict[str, Any]:
    """Connect to a server, read its tools and register them."""
    from app.core.database import SessionLocal
    from app.services import mcp_client

    own = db is None
    db = db or SessionLocal()
    try:
        config = mcp_config(name) or {}
        row = _row(db, "mcp", name)
        label = (row.label if row else None) or config.get("label") or name
        try:
            conn = connection(name)
            conn.connect()
            definitions = conn.list_tools()
        except mcp_client.NeedsSignIn as exc:
            _needs[name] = exc.resource_metadata
            _set_status(db, name, "needs_sign_in", "Sign in to use it")
            return {"ok": False, "status": "needs_sign_in", "detail": f"{label} needs you to sign in."}
        except Exception as exc:  # noqa: BLE001 - recorded on the add-on
            _drop_connection(name)
            _set_status(db, name, "error", str(exc)[:500])
            return {"ok": False, "status": "error", "detail": str(exc)[:500]}
        config["tools_cache"] = definitions
        if conn.server_info.get("name") and not config.get("server_name"):
            config["server_name"] = conn.server_info.get("name")
        save_mcp_config(name, config)
        names = register_mcp_tools(name, definitions, label) if (row is None or row.enabled) else []
        _upsert(db, "mcp", name, status="ready", error=None, tool_count=len(definitions),
                tools=[d.get("name") for d in definitions])
        return {"ok": True, "status": "ready", "tools": names, "count": len(definitions),
                "instructions": conn.instructions}
    finally:
        if own:
            db.close()


def add_mcp(db, name: str, config: dict, added_by: str = "user", label: str = None,
            description: str = None, secret: Dict[str, Any] = None,
            connect: bool = True) -> Dict[str, Any]:
    """Add (or replace) an MCP server and load its tools."""
    name = slug(name)
    config = {k: v for k, v in config.items() if v not in (None, "", [], {})}
    config["label"] = label or config.get("label") or name
    config.setdefault("added_at", datetime.utcnow().isoformat())
    _drop_connection(name)
    save_mcp_config(name, config)
    if secret is not None:
        set_secret(name, {**get_secret(name), **secret})
    _upsert(db, "mcp", name, label=config["label"],
            description=description or config.get("description"),
            enabled=True, status="starting", error=None, added_by=added_by)
    outcome = refresh_mcp(db, name) if connect else {"ok": True, "status": "starting"}
    logger.info("addons", "MCP server added", {"name": name, "by": added_by,
                                               "status": outcome.get("status")})
    return {"name": name, "label": config["label"], **outcome}


def set_enabled(db, kind: str, name: str, enabled: bool) -> Dict[str, Any]:
    from app.services.agent.registry import unregister_owner

    row = _row(db, kind, name)
    if row is None:
        return {"ok": False, "detail": "No such add-on"}
    row.enabled = bool(enabled)
    db.commit()
    if kind == "mcp":
        if enabled:
            return refresh_mcp(db, name)
        unregister_owner(f"mcp:{name}")
        _drop_connection(name)
    elif kind == "site":
        from app.services.browser import generic

        generic.load(name) if enabled else generic.unload(name)
    return {"ok": True, "enabled": row.enabled}


def set_auto_approve(db, name: str, enabled: bool) -> Dict[str, Any]:
    row = _row(db, "mcp", name)
    if row is None:
        return {"ok": False, "detail": "No such add-on"}
    row.auto_approve = bool(enabled)
    db.commit()
    return {"ok": True, "auto_approve": row.auto_approve}


def remove(db, kind: str, name: str) -> Dict[str, Any]:
    """Remove an add-on, its tools and its saved keys."""
    from app.services.agent.registry import unregister_owner

    row = _row(db, kind, name)
    label = (row.label if row else None) or name
    removed_tools = 0
    if kind == "mcp":
        removed_tools = unregister_owner(f"mcp:{name}")
        _drop_connection(name)
        (folder("mcp") / f"{name}.json").unlink(missing_ok=True)
        set_secret(name, None)
    elif kind == "site":
        from app.services.browser import generic

        removed_tools = generic.unload(name)
        (folder("site") / f"{name}.json").unlink(missing_ok=True)
    elif kind == "skill":
        shutil.rmtree(folder("skill") / name, ignore_errors=True)
    elif kind == "app":
        (folder("app") / f"{name}.json").unlink(missing_ok=True)
    else:
        return {"ok": False, "detail": f"Unknown kind {kind}"}
    if row is not None:
        db.delete(row)
        db.commit()
    logger.info("addons", "Add-on removed", {"kind": kind, "name": name})
    return {"ok": True, "detail": f"Removed {label}", "tools_removed": removed_tools}


# ------------------------------------------------------------- sign-in
def begin_sign_in(name: str, open_browser=None) -> Dict[str, Any]:
    """Sign in to a remote server with OAuth, in the user's own browser."""
    from app.services import mcp_client

    config = mcp_config(name)
    if not config or not config.get("url"):
        return {"ok": False, "detail": "Only web-address servers sign in this way."}
    if (_sign_in.get(name) or {}).get("status") == "waiting":
        return {"ok": True, **_sign_in[name]}
    _sign_in[name] = {"status": "waiting", "detail": "Finish signing in in your browser."}

    def work():
        from app.core.database import SessionLocal

        try:
            secret = get_secret(name)
            kwargs = {"client": secret.get("oauth")}
            if open_browser is not None:
                kwargs["open_browser"] = open_browser
            oauth = mcp_client.sign_in(config["url"], _needs.get(name, ""), **kwargs)
            set_secret(name, {**get_secret(name), "oauth": oauth})
            _drop_connection(name)
            with SessionLocal() as db:
                outcome = refresh_mcp(db, name)
            _sign_in[name] = {"status": "connected" if outcome.get("ok") else "failed",
                              "detail": "Signed in." if outcome.get("ok") else outcome.get("detail")}
        except Exception as exc:  # noqa: BLE001 - shown on the add-on
            _sign_in[name] = {"status": "failed", "detail": str(exc)[:300]}

    threading.Thread(target=work, daemon=True, name=f"cerebro-mcp-signin-{name}").start()
    return {"ok": True, **_sign_in[name]}


def sign_in_state(name: str) -> Dict[str, Any]:
    return dict(_sign_in.get(name) or {"status": "idle"})


# ------------------------------------------------------- the MCP Registry
def search_registry(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Servers in the official MCP Registry matching ``query``, ready to add."""
    import requests

    params = {"search": query, "limit": max(limit * 3, 10), "version": "latest"}
    data = None
    for path in ("/v0.1/servers", "/v0/servers"):
        try:
            response = requests.get(REGISTRY_URL + path, params=params, timeout=20)
            if response.ok:
                data = response.json()
                break
        except (requests.RequestException, ValueError):
            continue
    if data is None:
        raise RuntimeError("The MCP Registry couldn't be reached")
    found, seen = [], set()
    for entry in data.get("servers") or []:
        server = entry.get("server", entry)
        option = registry_option(server)
        if option is None or server.get("name") in seen:
            continue
        seen.add(server.get("name"))
        found.append(option)
        if len(found) >= limit:
            break
    return found


def registry_option(server: dict) -> Optional[Dict[str, Any]]:
    """How to connect to one registry entry: a remote address if it has one,
    else a uvx/npx package. ``needs`` lists the keys the user must supply."""
    registry_name = server.get("name") or ""
    base = {"registry_name": registry_name, "description": (server.get("description") or "")[:300],
            "version": server.get("version"),
            "label": server.get("title") or registry_name.rsplit("/", 1)[-1].replace("-", " ").title()}
    for remote in server.get("remotes") or []:
        kind = remote.get("type")
        if kind in ("streamable-http", "sse") and str(remote.get("url", "")).startswith("http"):
            if "{" in remote["url"]:
                continue                          # templated address: needs values we lack
            needs = [{"name": h.get("name"), "description": h.get("description"),
                      "secret": bool(h.get("isSecret")), "where": "header"}
                     for h in remote.get("headers") or [] if h.get("isRequired")]
            return {**base, "config": {"transport": "http" if kind == "streamable-http" else "sse",
                                       "url": remote["url"], "source": "registry",
                                       "registry_name": registry_name}, "needs": needs}
    for package in server.get("packages") or []:
        kind = package.get("registryType")
        if (package.get("transport") or {}).get("type", "stdio") != "stdio":
            continue
        identifier, version = package.get("identifier"), package.get("version")
        if not identifier:
            continue
        if kind == "pypi":
            command, args = "uvx", [f"{identifier}=={version}" if version else identifier]
        elif kind == "npm":
            command, args = "npx", ["-y", f"{identifier}@{version}" if version else identifier]
        else:
            continue
        for argument in package.get("packageArguments") or []:
            if argument.get("type") == "positional" and argument.get("value"):
                args.append(str(argument["value"]))
            elif argument.get("type") == "named" and argument.get("name") and argument.get("value"):
                args += [argument["name"], str(argument["value"])]
        needs = [{"name": v.get("name"), "description": v.get("description"),
                  "secret": bool(v.get("isSecret")), "where": "env"}
                 for v in package.get("environmentVariables") or [] if v.get("isRequired")]
        return {**base, "config": {"transport": "stdio", "command": command, "args": args,
                                   "source": "registry", "registry_name": registry_name},
                "needs": needs}
    return None


# --------------------------------------------------- the "Added" record
def record_added(db, kind: str, name: str, label: str, detail: str, added_by: str) -> dict:
    """An Activity entry and card for a new add-on, whose Undo removes it."""
    from app.services.agent import actions

    kind_label = {"mcp": "MCP", "site": "website", "skill": "skill", "app": "desktop app"}[kind]
    action = actions.propose(db, "capability_added", "addons", f"Added {label} ({kind_label})",
                             {"kind": kind, "name": name, "detail": detail},
                             preview={"fields": [{"name": label, "before": "", "after": detail}]},
                             summary=f"{'Cerebro' if added_by == 'cerebro' else 'You'} added it")
    actions.approve(db, action.id)
    db.refresh(action)
    card = actions.card(action)
    card["undo_label"] = "Remove"
    return card


def _register_actions() -> None:
    from app.services.agent import actions

    @actions.executor("capability_added")
    def _added(db, args: dict) -> dict:
        return {"detail": args.get("detail") or "Added",
                "undo": {"kind": args["kind"], "name": args["name"]}}

    @actions.undoer("capability_added")
    def _remove(db, undo: dict) -> dict:
        outcome = remove(db, undo["kind"], undo["name"])
        return {"detail": outcome.get("detail") or "Removed"}

    @actions.executor("mcp_call")
    def _call(db, args: dict) -> dict:
        from app.services import mcp_client

        server = args["server"]
        try:
            result = connection(server).call_tool(args["tool"], args.get("arguments") or {})
        except mcp_client.OutcomeUnknown as exc:
            raise RuntimeError(f"{exc}. It may or may not have happened in {server} — check there "
                               "before trying again.") from exc
        text = mcp_client.result_text(result, 4000)
        if result.get("isError"):
            raise RuntimeError(text[:500])
        mark_used(db, "mcp", server)
        return {"detail": text[:600], "output": text}

    @actions.executor("add_mcp_server")
    def _add_local(db, args: dict) -> dict:
        outcome = add_mcp(db, args["name"], args["config"], added_by="cerebro",
                          label=args.get("label"), description=args.get("description"),
                          secret=args.get("secret"))
        if not outcome.get("ok") and outcome.get("status") != "needs_sign_in":
            raise RuntimeError(outcome.get("detail") or "It didn't start")
        return {"detail": f"{outcome['label']} added — {outcome.get('count', 0)} tools",
                "undo": {"kind": "mcp", "name": outcome["name"]}}

    @actions.undoer("add_mcp_server")
    def _undo_local(db, undo: dict) -> dict:
        return {"detail": remove(db, undo["kind"], undo["name"]).get("detail")}


_register_actions()


# --------------------------------------------------------- export/import
def export_all() -> Dict[str, Any]:
    """Every add-on's definition, without keys or tokens."""
    from app.services import skills

    servers = []
    for path in sorted(folder("mcp").glob("*.json")):
        config = _read_json(path) or {}
        config.pop("tools_cache", None)
        servers.append({"name": path.stem, **config})
    sites = [{"name": p.stem, **(_read_json(p) or {})} for p in sorted(folder("site").glob("*.json"))]
    return {"format": EXPORT_FORMAT, "exported_at": datetime.utcnow().isoformat(),
            "mcp": servers, "sites": sites, "skills": skills.export_all()}


def import_all(db, data: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import skills
    from app.services.browser import generic

    if not isinstance(data, dict) or not str(data.get("format", "")).startswith("cerebro-addons"):
        raise ValueError("That isn't a Cerebro add-ons file")
    added = []
    for server in data.get("mcp") or []:
        name = server.pop("name", None) or server.get("label")
        if name:
            # Imported programs were chosen by a person sharing their setup.
            outcome = add_mcp(db, name, {**server, "source": server.get("source") or "user"},
                              added_by="user", connect=False)
            threading.Thread(target=refresh_mcp, args=(None, outcome["name"]), daemon=True).start()
            added.append(outcome["label"])
    for site in data.get("sites") or []:
        name = site.pop("name", None) or site.get("label")
        if name and site.get("start_url"):
            added.append(generic.add(db, site.get("label") or name, site["start_url"],
                                     extra=site, added_by="user")["label"])
    for skill in data.get("skills") or []:
        if skill.get("name") and skill.get("instructions"):
            added.append(skills.save(db, skill["name"], skill.get("description") or "",
                                     skill["instructions"], added_by="user")["name"])
    return {"ok": True, "added": added}


# ---------------------------------------------------------------- startup
def load_all(db=None, connect: bool = True) -> None:
    """Register every saved add-on's tools; connect MCP servers in the background."""
    from app.core.database import SessionLocal
    from app.services import skills
    from app.services.browser import generic

    own = db is None
    db = db or SessionLocal()
    try:
        for path in sorted(folder("mcp").glob("*.json")):
            name = path.stem
            config = _read_json(path) or {}
            row = _row(db, "mcp", name) or _upsert(
                db, "mcp", name, label=config.get("label") or name, enabled=True,
                status="starting", added_by=config.get("added_by") or "user")
            if row.enabled and config.get("tools_cache"):
                register_mcp_tools(name, config["tools_cache"], row.label or name)
        generic.load_all(db)
        skills.sync(db)
        names = [r["name"] for r in list_all(db) if r["kind"] == "mcp" and r["enabled"]]
    finally:
        if own:
            db.close()
    if connect:
        for name in names:
            threading.Thread(target=refresh_mcp, args=(None, name), daemon=True,
                             name=f"cerebro-mcp-{name}").start()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_start, daemon=True, name="cerebro-addons").start()


def _start() -> None:
    try:
        load_all()
    except Exception as exc:  # noqa: BLE001 - add-ons never stop Cerebro starting
        logger.error("addons", "Loading add-ons failed", {"error": str(exc)[:300]})


def stop() -> None:
    with _lock:
        names = list(_connections)
    for name in names:
        _drop_connection(name)
