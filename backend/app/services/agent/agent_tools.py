"""
Tools for teamwork and long-running work.

* ``delegate_to_agent`` — hand a job to another agent (it runs in the
  background and posts in that agent's chat).
* ``remember`` — write to the memory every agent shares.
* ``ask_user`` / ``update_plan`` — what a job uses to ask for input and to
  keep its plan current.
* ``create_project`` / ``tick_milestone`` — group work under a goal.
* ``create_watch`` — re-check a page on a schedule and alert on a change.
"""

import json
import re
import uuid
from typing import Any

from app.services import agents
from app.services.agent.registry import ToolContext, schema, string_param, tool


def _me(ctx: ToolContext):
    return agents.get(ctx.db, ctx.context.get("agent_id"))


@tool("list_agents", "List the user's Cerebro agents and what each is for.", schema([]),
      label="List agents")
def list_agents(ctx: ToolContext, **_) -> dict:
    rows = agents.listing(ctx.db)
    return {"content": "\n".join(f"- {a['name']}: {a['role'] or 'general'}"
                                 + (" (you)" if a["id"] == ctx.context.get("agent_id") else "")
                                 for a in rows), "summary": f"{len(rows)} agent(s)"}


@tool("delegate_to_agent",
      "Hand a job to another of the user's agents (by name). It works on it in the background "
      "and reports in its own chat. Use it when the work fits another agent's role or tools.",
      schema(["agent", "job"], agent=string_param("The agent's name."),
             job=string_param("What it should do, in full."),
             project=string_param("Optional project name or id to file it under.")),
      label="Hand over to an agent", activity="writing")
def delegate_to_agent(ctx: ToolContext, agent: str = "", job: str = "", project: str = "",
                      **_) -> dict:
    from app.models.task import Task
    from app.services import jobs
    from app.services.conversations import title_from

    target = agents.find(ctx.db, agent)
    if target is None:
        names = ", ".join(a["name"] for a in agents.listing(ctx.db))
        return {"content": f"No agent called {agent}. Agents: {names}", "summary": "Not found"}
    if not job.strip():
        return {"content": "Say what the job is.", "summary": "Needs a job"}
    me = _me(ctx)
    found = _project(ctx, project) if project else None
    task = Task(title=title_from(job)[:80], instruction=job, kind="agent",
                spec=json.dumps({"prompt": job, "from_agent": me.name}), schedule="once",
                status="queued", source="agent", agent_id=target.id,
                project_id=found.id if found else None, next_run=jobs._now())
    ctx.db.add(task)
    ctx.db.commit()
    jobs.event(ctx.db, task.id, "status", f"Handed over by {me.name}", job[:500])
    jobs.start_now(task.id)
    return {"content": f"Handed to {target.name} as job #{task.id}; it's starting now and will "
                       "report in its own chat.", "summary": f"Handed to {target.name}",
            "task": task.to_dict()}


@tool("remember",
      "Remember a fact, preference or fix for later — every agent shares this memory. Use it "
      "for things worth knowing next time (how the user likes something done, what fixed an "
      "issue, who looks after a customer).",
      schema(["fact"], fact=string_param("The thing to remember, in one or two sentences."),
             title=string_param("A short title (optional)."),
             case=string_param("A case number it belongs to (optional)."),
             customer=string_param("A customer it belongs to (optional).")),
      label="Remember", activity="writing")
def remember(ctx: ToolContext, fact: str = "", title: str = "", case: str = "", customer: str = "",
             **_) -> dict:
    from app.services.memory_service import MemoryService

    if not fact.strip():
        return {"content": "Nothing to remember.", "summary": "Empty"}
    memory = MemoryService(ctx.db).remember(
        title or fact[:60], fact, memory_type="fact", case_id=case or ctx.context.get("crm_case"),
        customer=customer or None, source=f"agent:{_me(ctx).name}", confidence=0.7)
    return {"content": f"Remembered (memory #{memory.id}). Every agent can recall it.",
            "summary": "Remembered"}


@tool("ask_user",
      "Ask the user something only they can answer, then stop. In a background job, the job "
      "waits for their answer and then carries on.",
      schema(["question"], question=string_param("The question, short and specific.")),
      label="Ask you", activity="awaiting_approval")
def ask_user(ctx: ToolContext, question: str = "", **_) -> dict:
    if not question.strip():
        return {"content": "Give the question.", "summary": "Needs a question"}
    if ctx.context.get("task_run"):
        ctx.context["_question"] = question.strip()
        return {"content": "Question passed to the user. Stop now: end your turn by asking it "
                           "plainly.", "summary": "Waiting for your answer"}
    return {"content": "You're talking with the user directly: just ask them in your reply.",
            "summary": "Ask in the reply"}


@tool("update_plan",
      "Set out or update your plan for a job: one step per line, each marked [done], "
      "[running] or [pending].",
      schema(["steps"], steps=string_param("Steps, one per line, e.g. '[done] Read the case'.")),
      mode="write", label="Update the plan", activity="thinking")
def update_plan(ctx: ToolContext, steps: Any = "", **_) -> dict:
    lines = steps if isinstance(steps, list) else str(steps or "").splitlines()
    parsed = []
    for line in lines:
        text = str(line).strip(" -•*\t")
        if not text:
            continue
        status = "pending"
        match = re.match(r"^\[(done|running|pending|failed|waiting|x| )\]\s*(.+)$", text, re.I)
        if match:
            mark = match.group(1).lower()
            status = {"x": "done", " ": "pending"}.get(mark, mark)
            text = match.group(2)
        parsed.append({"title": text[:200], "status": status})
    if not parsed:
        return {"content": "Give the steps, one per line.", "summary": "No steps"}
    ctx.context["_plan_steps"] = parsed
    if ctx.emit:
        ctx.emit({"type": "plan_update", "title": "Plan updated", "steps": parsed})
    done = sum(1 for p in parsed if p["status"] == "done")
    return {"content": f"Plan updated ({done}/{len(parsed)} done).", "summary": f"{done}/{len(parsed)} done"}


# --------------------------------------------------------------- projects
def _project(ctx: ToolContext, ref: str):
    from app.models.work import Project

    ref = (ref or "").strip()
    if ref.isdigit():
        return ctx.db.get(Project, int(ref))
    if not ref:
        return None
    return (ctx.db.query(Project).filter(Project.title.ilike(f"%{ref}%"))
            .order_by(Project.id.desc()).first())


@tool("create_project",
      "Create a project (a goal with milestones) to group related jobs, e.g. 'Migrate the "
      "clinic's imaging PCs' with its milestones.",
      schema(["title"], title=string_param("The project's name."),
             description=string_param("What it's for."),
             milestones=string_param("Milestones, one per line.")),
      mode="write", label="Create a project", activity="writing")
def create_project(ctx: ToolContext, title: str = "", description: str = "", milestones: Any = "",
                   **_) -> dict:
    from app.models.work import Project

    if not title.strip():
        return {"content": "Give the project a name.", "summary": "Needs a name"}
    lines = milestones if isinstance(milestones, list) else str(milestones or "").splitlines()
    items = [{"id": uuid.uuid4().hex[:8], "title": str(m).strip(" -•*\t")[:160], "done": False}
             for m in lines if str(m).strip(" -•*\t")]
    project = Project(agent_id=ctx.context.get("agent_id"), title=title.strip()[:120],
                      description=description.strip() or None, milestones=json.dumps(items))
    ctx.db.add(project)
    ctx.db.commit()
    return {"content": f"Project #{project.id} “{project.title}” created with {len(items)} "
                       "milestone(s).", "summary": "Project created"}


@tool("tick_milestone", "Mark a project milestone done (or not done).",
      schema(["project", "milestone"], project=string_param("Project name or id."),
             milestone=string_param("The milestone's title (or part of it)."),
             done=string_param("'yes' (default) or 'no'.")),
      mode="write", label="Tick a milestone", activity="writing")
def tick_milestone(ctx: ToolContext, project: str = "", milestone: str = "", done: str = "yes",
                   **_) -> dict:
    found = _project(ctx, project)
    if found is None:
        return {"content": f"No project matching {project}.", "summary": "Not found"}
    items = found.milestone_list()
    wanted = milestone.strip().lower()
    for item in items:
        if wanted and (wanted == item["id"] or wanted in item["title"].lower()):
            item["done"] = str(done).lower() not in ("no", "false", "0")
            found.milestones = json.dumps(items)
            if items and all(m["done"] for m in items):
                found.status = "done"
            ctx.db.commit()
            return {"content": f"“{item['title']}” marked {'done' if item['done'] else 'not done'}.",
                    "summary": "Milestone updated"}
    return {"content": f"No milestone matching {milestone}.", "summary": "Not found"}


# ----------------------------------------------------------------- watches
@tool("create_watch",
      "Keep an eye on a web page (a case, a portal, a status page) and alert the user when it "
      "changes, when some text appears or disappears, or when a number goes below/above a "
      "value. Pages on connected sites are read signed in.",
      schema(["url"], url=string_param("The page's address."),
             title=string_param("What it's watching, e.g. 'CAS-01234 status'."),
             condition=string_param("change (default) | contains | absent | below | above"),
             value=string_param("The text, or the number, for the condition."),
             every_minutes=string_param("How often to check (minutes, at least 5; default 60)."),
             then=string_param("Optional: a job to run when it fires.")),
      mode="write", label="Watch a page", activity="writing")
def create_watch(ctx: ToolContext, url: str = "", title: str = "", condition: str = "change",
                 value: str = "", every_minutes: Any = 60, then: str = "", **_) -> dict:
    from app.services import watches

    try:
        watch = watches.create(ctx.db, url, title=title, condition=condition, value=value,
                               interval_minutes=every_minutes, follow_up=then,
                               agent_id=ctx.context.get("agent_id"))
    except ValueError as exc:
        return {"content": str(exc), "summary": "Couldn't watch it"}
    return {"content": f"Watching “{watch.title}” every {watch.interval_minutes} minutes "
                       f"({watches.describe(watch)}). The first check runs now.",
            "summary": "Watching"}
