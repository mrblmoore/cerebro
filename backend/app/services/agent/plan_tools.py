"""``show_plan``: a short visible plan before a job with several steps.

Shown as a card as soon as it's made, so the user sees what Cerebro is about
to do (and can stop it) before the work starts; the job then gets more steps
to finish in.
"""

from typing import Any, List

from app.services.agent.registry import ToolContext, schema, string_param, tool


@tool("show_plan",
      "Show the user a short plan before a job that needs several steps (more than two tools). "
      "Then carry it out.",
      schema(["steps"], steps=string_param("The steps, one per line (3–7 short lines)."),
             goal=string_param("One line: what the job achieves.")),
      mode="write", label="Plan", activity="thinking")
def show_plan(ctx: ToolContext, steps: Any = "", goal: str = "", **_) -> dict:
    if isinstance(steps, list):
        lines: List[str] = [str(s).strip() for s in steps]
    else:
        lines = [line.strip(" -•*\t").strip() for line in str(steps or "").splitlines()]
    lines = [line.lstrip("0123456789.) ").strip() for line in lines if line.strip()][:10]
    if not lines:
        return {"content": "Give the steps, one per line.", "summary": "No steps"}
    card = {"type": "plan", "status": "running", "title": goal or "Plan", "steps": lines}
    ctx.context["_plan"] = card
    if ctx.emit:
        ctx.emit(card)
    return {"content": "Plan shown to the user. Now do the steps.", "summary": f"{len(lines)} steps"}
