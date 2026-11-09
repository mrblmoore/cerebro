"""
Agents: several named Cerebro workers sharing one brain.

Every agent uses the same memory, add-ons, skills and settings. Each has its
own name and role, standing instructions, chats, jobs, face and voice — and
optionally a *tool scope*: the groups of tools it may use ("only Outlook and
Teams"). An empty scope means everything.

The default agent ("Cerebro") always exists; chats and jobs with no agent
belong to it, so everything from before agents keeps working.
"""

import json
from typing import Any, Dict, List, Optional

from app.core import logger

#: Tool groups an agent's scope is made of, in the order the editor shows them.
GROUPS = {
    "knowledge": "Knowledge, sources and documents",
    "cases": "Dynamics 365 cases",
    "kb": "RightAnswers articles",
    "sharepoint": "SharePoint",
    "messaging": "Outlook and Teams",
    "systems": "BeyondTrust and Genesys",
    "files": "Files, folders and web pages",
    "commands": "Commands on this PC",
    "web": "Browsing connected sites",
    "addons": "Add-ons (MCP servers and websites)",
    "self-extend": "Adding new connections",
    "desktop": "Windows desktop apps",
    "calendar": "Calendar and meetings",
    "secretary": "Follow-ups, to-dos, briefings and documents",
}

_PREFIXES = (
    ("draft_document", "secretary"), ("edit_document", "files"), ("app_", "desktop"), ("add_desktop_app", "desktop"),
    ("calendar_", "calendar"), ("find_free_time", "calendar"), ("book_meeting", "calendar"),
    ("reschedule_meeting", "calendar"), ("cancel_meeting", "calendar"),
    ("followup", "secretary"), ("add_followup", "secretary"), ("todo", "secretary"),
    ("add_todo", "secretary"), ("done_todo", "secretary"), ("briefing", "secretary"),
    ("meeting_notes", "secretary"), ("list_followups", "secretary"), ("chase_followup", "secretary"),
    ("dynamics_", "cases"), ("rightanswers_", "kb"), ("sharepoint_", "sharepoint"),
    ("search_sharepoint", "sharepoint"), ("outlook_", "messaging"), ("teams_", "messaging"),
    ("send_", "messaging"), ("reply_", "messaging"), ("draft_", "messaging"),
    ("get_inbox", "messaging"), ("get_message_thread", "messaging"),
    ("beyondtrust_", "systems"), ("genesys_", "systems"), ("link_call", "systems"),
    ("run_command", "commands"), ("browse_", "web"),
    ("list_folder", "files"), ("find_in_files", "files"), ("read_file", "files"),
    ("write_file", "files"), ("review_log", "files"), ("fetch_url", "files"),
    ("find_connector", "self-extend"), ("add_mcp_server", "self-extend"),
    ("add_website", "self-extend"), ("remove_capability", "self-extend"),
    ("list_addons", "self-extend"),
)

#: Always available, whatever the scope: thinking, memory and teamwork.
CORE = {"show_plan", "find_tools", "use_skill", "save_skill", "list_skills", "recall_memory",
        "remember", "ask_user", "update_plan", "delegate_to_agent", "list_agents",
        "create_task", "create_project", "tick_milestone", "create_watch",
        "get_current_context", "keep_updated", "list_updates"}

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "case-researcher": {
        "name": "Case researcher", "role": "Researches cases and finds the fix",
        "instructions": "Work from the case in Dynamics. Search RightAnswers, the knowledge base "
                        "and SharePoint for the cause and fix; cite what you find; suggest the "
                        "next step and a note for the case.",
        "tool_scope": ["knowledge", "cases", "kb", "sharepoint", "files", "web"],
        "face": "anime", "face_style": {"palette": "blue", "hair": "bob"}, "color": "#38bdf8"},
    "inbox": {
        "name": "Inbox assistant", "role": "Keeps on top of Outlook and Teams",
        "instructions": "Triage new mail and chats, draft replies in the user's voice, and "
                        "flag anything urgent. Never send without approval.",
        "tool_scope": ["knowledge", "messaging", "cases"],
        "face": "anime", "face_style": {"palette": "pink", "hair": "twintails"}, "color": "#f472b6"},
    "kb-writer": {
        "name": "KB writer", "role": "Turns fixes into knowledge articles",
        "instructions": "Write clear, step-by-step knowledge articles from resolved cases and "
                        "repeated questions. Check RightAnswers for an existing article first.",
        "tool_scope": ["knowledge", "kb", "sharepoint", "cases", "files"],
        "face": "anime", "face_style": {"palette": "brown", "hair": "long"}, "color": "#f59e0b"},
    "watcher": {
        "name": "Watcher", "role": "Keeps an eye on pages and statuses",
        "instructions": "Set up watches for what the user wants followed, and when one fires, "
                        "say plainly what changed and what to do about it.",
        "tool_scope": ["knowledge", "web", "files", "cases"],
        "face": "anime", "face_style": {"palette": "green", "hair": "sideponytail"}, "color": "#34d399"},
    "blank": {"name": "New agent", "role": "", "instructions": "", "tool_scope": [],
              "face": "anime", "face_style": {"palette": "silver"}, "color": "#a78bfa"},
}


def group_of(tool) -> str:
    """The scope group a tool belongs to."""
    name = tool.name if hasattr(tool, "name") else str(tool)
    owner = getattr(tool, "owner", "") or ""
    if name in CORE:
        return "core"
    if owner:
        return "addons"
    for prefix, group in _PREFIXES:
        if name.startswith(prefix):
            return group
    return "knowledge"


def in_scope(tool, scope: Optional[List[str]]) -> bool:
    if not scope:
        return True
    group = group_of(tool)
    if group == "core":
        return True
    owner = getattr(tool, "owner", "") or ""
    return group in scope or (owner and owner in scope)


# ------------------------------------------------------------------ store
def default_agent(db):
    from app.models.agent import Agent

    agent = db.query(Agent).filter(Agent.is_default.is_(True)).first()
    if agent is None:
        agent = Agent(name="Cerebro", role="Your all-round copilot", face="brain",
                      color="#8b7cf6", is_default=True, sort=0, template="default")
        db.add(agent)
        db.commit()
        db.refresh(agent)
    return agent


def ensure_default() -> None:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        default_agent(db)
    except Exception as exc:  # noqa: BLE001 - never block start-up
        db.rollback()
        logger.warn("agents", "Could not create the default agent", {"error": str(exc)})
    finally:
        db.close()


def get(db, agent_id: Optional[int]):
    from app.models.agent import Agent

    if agent_id:
        agent = db.get(Agent, int(agent_id))
        if agent is not None:
            return agent
    return default_agent(db)


def find(db, name: str):
    """An agent by (part of) its name, or ``None``."""
    from app.models.agent import Agent

    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    agents = db.query(Agent).filter(Agent.archived.is_(False)).all()
    for agent in agents:
        if agent.name.lower() == wanted:
            return agent
    for agent in agents:
        if wanted in agent.name.lower() or agent.name.lower().split(" ")[0] == wanted.split(" ")[0]:
            return agent
    return None


def listing(db, archived: bool = False) -> List[Dict[str, Any]]:
    from app.models.agent import Agent

    default_agent(db)
    rows = (db.query(Agent).filter(Agent.archived.is_(bool(archived)))
            .order_by(Agent.is_default.desc(), Agent.sort, Agent.id).all())
    return [row.to_dict() for row in rows]


_FIELDS = ("name", "role", "instructions", "face", "voice_name", "voice_rate", "color", "sort",
           "archived")


def create(db, template: str = "blank", **fields):
    from app.models.agent import Agent

    base = dict(TEMPLATES.get(template) or TEMPLATES["blank"])
    base.update({k: v for k, v in fields.items() if v is not None})
    agent = Agent(template=template if template in TEMPLATES else "blank")
    _apply(agent, base)
    if not agent.name:
        agent.name = "New agent"
    db.add(agent)
    db.commit()
    db.refresh(agent)
    logger.info("agents", "Agent created", {"id": agent.id, "name": agent.name})
    return agent


def update(db, agent, **fields):
    _apply(agent, fields)
    if agent.is_default:
        agent.archived = False
    db.commit()
    db.refresh(agent)
    return agent


def _apply(agent, fields: Dict[str, Any]) -> None:
    for key in _FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "name":
                value = str(value).strip()[:60] or agent.name
            if key == "face" and value not in ("brain", "anime", "live2d", "vrm"):
                continue
            setattr(agent, key, value)
    if fields.get("face_style") is not None:
        from app.services.personality import clean_face_style

        agent.face_style = json.dumps(clean_face_style(fields["face_style"]))
    if fields.get("voice_style") is not None:
        from app.services.personality import clean_voice

        agent.voice_style = json.dumps(clean_voice(fields["voice_style"]))
    if fields.get("personality") is not None:
        from app.services.personality import clean_personality

        agent.personality = json.dumps(clean_personality(fields["personality"]))
    if fields.get("tool_scope") is not None:
        scope = [s for s in fields["tool_scope"] if s in GROUPS or str(s).startswith(("mcp:", "site:"))]
        agent.tool_scope = json.dumps(scope)


def archive(db, agent) -> None:
    if agent.is_default:
        raise ValueError("The default agent can't be removed.")
    agent.archived = True
    db.commit()


# ------------------------------------------------------------ for the loop
def context_for(agent) -> Dict[str, Any]:
    """What ``loop.run`` needs to know about the agent doing the work."""
    if agent is None:
        return {}
    return {"agent_id": agent.id, "agent_name": agent.name, "tool_scope": agent.scope()}


def identity_block(db, agent) -> str:
    """The short "who you are, who else is on the team" note for the model."""
    from app.models.agent import Agent

    if agent is None:
        return ""
    others = [a for a in db.query(Agent).filter(Agent.archived.is_(False), Agent.id != agent.id)]
    lines = [f"You are {agent.name}" + (f", {agent.role}" if agent.role else "") +
             ", one of the user's Cerebro agents. You all share the same memory."]
    if others:
        lines.append("The other agents: " + "; ".join(
            f"{a.name}" + (f" ({a.role})" if a.role else "") for a in others[:12])
            + ". Hand work that fits one of them better to it with delegate_to_agent.")
    scope = agent.scope()
    if scope:
        lines.append("You may use only these kinds of tools: " + ", ".join(
            GROUPS.get(s, s) for s in scope) + ". If a request needs something else, say so "
            "and offer to hand it to an agent that can.")
    from app.services.personality import persona_block

    persona = persona_block(agent)
    if persona:
        lines.append(persona)
    if (agent.instructions or "").strip():
        lines.append("Your standing instructions from the user:\n" + agent.instructions.strip())
    return "\n".join(lines)
