"""
API for agents and their work: jobs, projects, watches, ideas, and the Home
board that brings it all together.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.services import agents, ideas, jobs, watches

router = APIRouter(prefix="/api", tags=["agents"], dependencies=[Depends(require_local_origin)])


# ------------------------------------------------------------------ agents
class AgentIn(BaseModel):
    template: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    instructions: Optional[str] = None
    face: Optional[str] = None
    face_style: Optional[Dict[str, Any]] = None
    voice_name: Optional[str] = None
    voice_rate: Optional[float] = None
    tool_scope: Optional[List[str]] = None
    color: Optional[str] = None
    sort: Optional[int] = None


def _agent(db, agent_id: int):
    from app.models.agent import Agent

    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="No such agent")
    return agent


def _status(db, agent_id: int, default_id: int) -> Dict[str, Any]:
    from app.models.task import Task
    from app.models.work import Idea

    mine = (Task.agent_id == agent_id) | ((Task.agent_id.is_(None)) if agent_id == default_id else False)
    counts = {status: db.query(Task.id).filter(mine, Task.status == status).count()
              for status in ("running", "queued", "waiting_input", "waiting_approval")}
    idea_filter = (Idea.agent_id == agent_id) | ((Idea.agent_id.is_(None)) if agent_id == default_id else False)
    counts["ideas"] = db.query(Idea.id).filter(idea_filter, Idea.status == "new").count()
    current = (db.query(Task).filter(mine, Task.status == "running")
               .order_by(Task.updated_at.desc()).first())
    state = ("needs_input" if counts["waiting_input"] else
             "needs_approval" if counts["waiting_approval"] else
             "working" if counts["running"] or counts["queued"] else "idle")
    return {"state": state, "counts": counts,
            "current": current.to_dict() if current else None}


@router.get("/agents")
def list_agents(archived: bool = False, db: Session = Depends(get_db)) -> Dict[str, Any]:
    items = agents.listing(db, archived)
    default_id = agents.default_agent(db).id
    for item in items:
        item["status"] = _status(db, item["id"], default_id)
    return {"agents": items, "groups": agents.GROUPS,
            "templates": {k: {"name": v["name"], "role": v["role"]} for k, v in agents.TEMPLATES.items()}}


@router.post("/agents")
def create_agent(body: AgentIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    fields = body.dict(exclude_none=True)
    template = fields.pop("template", "blank")
    return agents.create(db, template, **fields).to_dict()


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = _agent(db, agent_id)
    return {**agent.to_dict(), "status": _status(db, agent.id, agents.default_agent(db).id)}


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: int, body: AgentIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return agents.update(db, _agent(db, agent_id), **body.dict(exclude_none=True)).to_dict()


@router.delete("/agents/{agent_id}")
def archive_agent(agent_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        agents.archive(db, _agent(db, agent_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# -------------------------------------------------------------------- jobs
class JobIn(BaseModel):
    instruction: str
    agent_id: Optional[int] = None
    project_id: Optional[int] = None
    milestone_id: Optional[str] = None
    schedule: str = "once"
    at_time: Optional[str] = None
    title: Optional[str] = None
    run_now: bool = True


def _task(db, task_id: int):
    from app.models.task import Task

    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No such job")
    return task


@router.get("/jobs")
def list_jobs(agent_id: Optional[int] = None, project_id: Optional[int] = None,
              status: Optional[str] = "open", db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {"jobs": jobs.listing(db, agent_id, project_id, status)}


@router.post("/jobs")
def create_job(body: JobIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.task import Task
    from app.services.conversations import title_from
    from app.services.task_service import SCHEDULES, compute_next_run

    if not body.instruction.strip():
        raise HTTPException(status_code=400, detail="Say what the job is.")
    agent = agents.get(db, body.agent_id)
    schedule = body.schedule if body.schedule in SCHEDULES else "once"
    once_now = schedule == "once" and not body.at_time
    task = Task(title=(body.title or title_from(body.instruction))[:80],
                instruction=body.instruction.strip(), kind="agent",
                spec=json.dumps({"prompt": body.instruction.strip()}), schedule=schedule,
                at_time=body.at_time, status="queued" if once_now else "active", source="user",
                agent_id=agent.id, project_id=body.project_id, milestone_id=body.milestone_id,
                next_run=jobs._now() if once_now else compute_next_run(schedule, body.at_time))
    db.add(task)
    db.commit()
    jobs.event(db, task.id, "status", "Created", f"For {agent.name}")
    if body.run_now and once_now:
        jobs.start_now(task.id)
    return task.to_dict()


@router.get("/jobs/{task_id}")
def get_job(task_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    task = _task(db, task_id)
    return {**task.to_dict(), "events": jobs.events(db, task.id)}


@router.post("/jobs/{task_id}/{action}")
def control_job(task_id: int, action: str, body: Dict[str, Any] = None,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    task = _task(db, task_id)
    if action == "answer":
        result = jobs.answer(db, task, (body or {}).get("answer") or "")
        if result.get("ok"):
            jobs.start_now(task.id)
        return result
    if action == "run":
        jobs.start_now(task.id)
        return {"ok": True}
    result = jobs.control(db, task, action)
    if action in ("resume", "retry") and result.get("ok") and task.schedule == "once":
        jobs.start_now(task.id)
    return result


# --------------------------------------------------------- keeping updated
@router.get("/updates")
def list_updates(agent_id: Optional[int] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What's being kept updated ("Keeping X updated" cards)."""
    from app.services import keeping

    return {"updates": keeping.listing(db, agent_id)}


@router.get("/updates/{task_id}")
def get_update(task_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services import keeping

    return keeping.card(db, _task(db, task_id))


@router.post("/updates/{task_id}/auto-apply")
def set_update_auto_apply(task_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """The target's own "apply changes automatically" switch."""
    from app.services import keeping

    task = _task(db, task_id)
    integration = (json.loads(task.spec or "{}").get("keep") or {}).get("integration")
    if not integration:
        raise HTTPException(status_code=400, detail="This one always needs approval.")
    try:
        keeping.set_auto_apply(integration, bool((body or {}).get("enabled")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    jobs.event(db, task.id, "status", "Updates by itself now" if (body or {}).get("enabled")
               else "Changes wait for approval again")
    return keeping.card(db, task)


# ---------------------------------------------------------------- projects
class ProjectIn(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    agent_id: Optional[int] = None
    status: Optional[str] = None
    milestones: Optional[List[Any]] = None


def _milestones(items: List[Any]) -> str:
    out = []
    for item in items or []:
        if isinstance(item, str):
            item = {"title": item}
        title = str(item.get("title") or "").strip()
        if title:
            out.append({"id": item.get("id") or uuid.uuid4().hex[:8], "title": title[:160],
                        "done": bool(item.get("done"))})
    return json.dumps(out)


@router.get("/projects")
def list_projects(agent_id: Optional[int] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.work import Project

    query = db.query(Project)
    if agent_id is not None:
        query = query.filter(Project.agent_id == agent_id)
    items = []
    for project in query.order_by(Project.status, Project.id.desc()):
        item = project.to_dict()
        item["jobs"] = jobs.listing(db, project_id=project.id, status=None, limit=50)
        items.append(item)
    return {"projects": items}


@router.post("/projects")
def create_project(body: ProjectIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.work import Project

    if not (body.title or "").strip():
        raise HTTPException(status_code=400, detail="Give the project a name.")
    project = Project(agent_id=body.agent_id, title=body.title.strip()[:120],
                      description=(body.description or "").strip() or None,
                      milestones=_milestones(body.milestones or []))
    db.add(project)
    db.commit()
    return project.to_dict()


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectIn, db: Session = Depends(get_db)):
    from app.models.work import Project

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="No such project")
    if body.title is not None:
        project.title = body.title.strip()[:120] or project.title
    if body.description is not None:
        project.description = body.description.strip() or None
    if body.status in ("active", "paused", "done"):
        project.status = body.status
    if body.agent_id is not None:
        project.agent_id = body.agent_id
    if body.milestones is not None:
        project.milestones = _milestones(body.milestones)
    db.commit()
    return project.to_dict()


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    from app.models.task import Task
    from app.models.work import Project

    project = db.get(Project, project_id)
    if project is not None:
        db.query(Task).filter(Task.project_id == project_id).update({Task.project_id: None})
        db.delete(project)
        db.commit()
    return {"ok": True}


# ----------------------------------------------------------------- watches
class WatchIn(BaseModel):
    url: str
    title: Optional[str] = None
    condition: str = "change"
    value: Optional[str] = None
    interval_minutes: int = 60
    follow_up: Optional[str] = None
    agent_id: Optional[int] = None


@router.get("/watches")
def list_watches(agent_id: Optional[int] = None, db: Session = Depends(get_db)):
    from app.models.work import Watch

    query = db.query(Watch).filter(Watch.status != "stopped")
    if agent_id is not None:
        query = query.filter(Watch.agent_id == agent_id)
    return {"watches": [dict(w.to_dict(), describe=watches.describe(w))
                        for w in query.order_by(Watch.id.desc())]}


@router.post("/watches")
def create_watch(body: WatchIn, db: Session = Depends(get_db)):
    try:
        watch = watches.create(db, body.url, body.title or "", body.condition, body.value or "",
                               body.interval_minutes, body.follow_up or "", body.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return watch.to_dict()


@router.post("/watches/{watch_id}/{action}")
def control_watch(watch_id: int, action: str, db: Session = Depends(get_db)):
    from app.models.work import Watch

    watch = db.get(Watch, watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="No such watch")
    return watches.control(db, watch, action)


# ------------------------------------------------------------------- ideas
@router.get("/ideas")
def list_ideas(agent_id: Optional[int] = None, status: str = "new", db: Session = Depends(get_db)):
    return {"ideas": ideas.listing(db, agent_id, status)}


@router.post("/ideas/refresh")
def refresh_ideas(db: Session = Depends(get_db)):
    return {"made": ideas.refresh(db)}


@router.post("/ideas/{idea_id}/{action}")
def decide_idea(idea_id: int, action: str, body: Dict[str, Any] = None,
                db: Session = Depends(get_db)):
    from app.models.work import Idea

    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="No such idea")
    body = body or {}
    result = ideas.decide(db, idea, action, body.get("prompt"), body.get("agent_id"))
    if result.get("task"):
        jobs.start_now(result["task"]["id"])
    return result


# -------------------------------------------------------------------- home
@router.get("/home")
def home(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything for the Home board: each agent's state and what needs the user."""
    from app.models.task import Task
    from app.models.work import Watch

    default_id = agents.default_agent(db).id
    board = []
    for item in agents.listing(db):
        item["status"] = _status(db, item["id"], default_id)
        item["jobs"] = jobs.listing(db, item["id"], status="open", limit=6)
        item["ideas"] = ideas.listing(db, item["id"], "new", limit=4)
        board.append(item)
    needs = [t.to_dict() for t in db.query(Task).filter(
        Task.status.in_(("waiting_input", "waiting_approval"))).order_by(Task.updated_at.desc()).limit(20)]
    fired = [w.to_dict() for w in db.query(Watch).filter(Watch.last_alert_at.isnot(None))
             .order_by(Watch.last_alert_at.desc()).limit(5)]
    from app.services import keeping

    return {"agents": board, "needs_you": needs, "watches_fired": fired,
            "updates": keeping.listing(db)[:8]}
