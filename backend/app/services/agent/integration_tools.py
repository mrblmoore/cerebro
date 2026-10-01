"""
Ask tools for the hidden-browser integrations: Dynamics 365 and RightAnswers.

Reads run immediately. Every change is *proposed*: the tool reads the current
state, records an approval card with a before/after preview, and stops. The
matching executor at the bottom of this module runs only after the user
approves the card.
"""

from typing import Any, Dict

from app.services.agent import actions
from app.services.agent.registry import ToolContext, schema, string_param, tool


def _dynamics():
    from app.services.browser import get

    return get("dynamics")


def _rightanswers():
    from app.services.browser import get

    return get("rightanswers")


def _dynamics_on(ctx: ToolContext) -> bool:
    return _dynamics().enabled


def _rightanswers_on(ctx: ToolContext) -> bool:
    return _rightanswers().enabled


def _guard(ctx: ToolContext, connector, fn) -> Dict[str, Any]:
    """Run a connector call, turning the expected failures into guidance."""
    from app.services.browser import BrowserUnavailable, NotConfigured, SignInRequired

    try:
        return fn()
    except SignInRequired:
        ctx.drafts.append({"type": "signin", "integration": connector.name,
                           "title": f"Sign in to {connector.label}",
                           "detail": "Cerebro's browser isn't signed in yet. Sign in once and "
                                     "ask again."})
        return {"content": f"Not signed in to {connector.label}. Tell the user to click "
                           f"“Sign in to {connector.label}” and then ask again.",
                "summary": "Sign-in needed"}
    except (NotConfigured, BrowserUnavailable) as exc:
        return {"content": str(exc), "summary": "Not available"}
    except Exception as exc:  # noqa: BLE001 - explained to the model and user
        return {"content": f"{connector.label} error: {exc}", "summary": "Failed"}


# ================================================================ Dynamics
@tool("dynamics_search_cases",
      "Search Dynamics 365 support cases by case number (CAS-…), title words, "
      "customer or description. Optionally only open or resolved cases.",
      schema([], query=string_param("Case number or words to look for. Empty lists recent cases."),
             status=string_param("'open', 'resolved' or empty for all.")),
      label="Search Dynamics cases", activity="browsing", available=_dynamics_on)
def dynamics_search_cases(ctx: ToolContext, query: str = "", status: str = "", **_) -> dict:
    connector = _dynamics()

    def run():
        cases = connector.search_cases(query, status=status)
        if not cases:
            return {"content": f"No Dynamics cases matched “{query}”.", "summary": "No matches"}
        lines = [f"{c['ticket']} · {c['title']} · {c.get('customer') or 'unknown customer'} · "
                 f"{c.get('status')} · priority {c.get('priority')} · modified {c.get('modified')}"
                 for c in cases]
        return {"content": "\n".join(lines), "summary": f"{len(cases)} case(s)"}

    return _guard(ctx, connector, run)


@tool("dynamics_get_case",
      "Read one Dynamics 365 case in full: fields, description, timeline notes "
      "and activities. Use the case number (CAS-…).",
      schema(["case"], case=string_param("Case number such as CAS-01234-ABCDEF, or its ID.")),
      label="Read Dynamics case", activity="browsing", available=_dynamics_on)
def dynamics_get_case(ctx: ToolContext, case: str = "", **_) -> dict:
    connector = _dynamics()

    def run():
        record = connector.get_case(case)
        text = connector.case_text(record)
        _remember_case(ctx.db, record, text)
        ref = ctx.cite({"title": f"{record['ticket']} — {record['title']}", "kind": "dynamics",
                        "uri": record.get("url"), "locator": "Dynamics 365",
                        "excerpt": text[:1000]}, "CRM")
        return {"content": f"[{ref}]\n{text}", "summary": record.get("ticket") or case}

    return _guard(ctx, connector, run)


def _remember_case(db, record: dict, text: str) -> None:
    """Keep the case as a source for follow-ups and mirror it locally."""
    from app.models.case import Case
    from app.services.source_service import SourceService

    SourceService(db).observe("crm_case", f"dynamics:{record.get('id')}",
                              f"{record.get('ticket')} — {record.get('title')}",
                              uri=record.get("url"), content=text,
                              metadata={"system": "dynamics", "ticket": record.get("ticket")})
    ticket = record.get("ticket")
    if not ticket:
        return
    row = db.query(Case).filter(Case.case_id == ticket).first()
    if row is None:
        row = Case(case_id=ticket, system="dynamics")
        db.add(row)
    row.title = record.get("title") or row.title
    row.customer = record.get("customer") or row.customer
    row.description = record.get("description") or row.description
    row.status = "closed" if str(record.get("state") or "").lower() in ("resolved", "cancelled") \
        else "open"
    row.external_id, row.url = record.get("id"), record.get("url")
    db.commit()


@tool("dynamics_add_note",
      "Propose adding a note to a Dynamics 365 case timeline. The user reviews "
      "and approves it before it is posted.",
      schema(["case", "text"], case=string_param("Case number (CAS-…)."),
             text=string_param("The note text."),
             subject=string_param("Short note title.")),
      mode="approval", label="Prepare a case note", activity="writing", available=_dynamics_on)
def dynamics_add_note(ctx: ToolContext, case: str = "", text: str = "", subject: str = "",
                      **_) -> dict:
    if not case.strip() or not text.strip():
        return {"content": "A note needs a case number and text.", "summary": "Needs details"}
    action = actions.propose(
        ctx.db, "dynamics_add_note", "dynamics", f"Add a note to {case}",
        {"case": case, "text": text, "subject": subject or "Note from Cerebro"},
        preview={"fields": [{"name": "Note", "before": "", "after": text}],
                 "target": case},
        summary=subject or text[:80])
    ctx.drafts.append(actions.card(action))
    return {"content": f"Note for {case} prepared as change #{action.id}; it will be "
                       "posted when the user approves it.", "summary": "Waiting for approval"}


@tool("dynamics_update_case",
      "Propose changing fields on a Dynamics 365 case: title, description, "
      "priority (High/Normal/Low), severity or status reason. The user approves "
      "a before/after preview first.",
      schema(["case", "fields"], case=string_param("Case number (CAS-…)."),
             fields={"type": "object", "description":
                     "Field → new value, e.g. {\"priority\": \"High\"}.",
                     "additionalProperties": True}),
      mode="approval", label="Prepare a case update", activity="writing", available=_dynamics_on)
def dynamics_update_case(ctx: ToolContext, case: str = "", fields: dict = None, **_) -> dict:
    from app.services.browser.dynamics import DynamicsConnector, DynamicsError

    connector = _dynamics()
    try:
        # Field names are checked now; option labels are checked against the
        # organisation's own metadata in prepare_update below.
        DynamicsConnector.field_names(fields or {})
    except DynamicsError as exc:
        return {"content": str(exc), "summary": "Not allowed"}

    def run():
        try:
            prepared = connector.prepare_update(case, fields or {})
        except DynamicsError as exc:
            return {"content": str(exc), "summary": "Not allowed"}
        current, rows = prepared["case"], prepared["rows"]
        action = actions.propose(
            ctx.db, "dynamics_update_case", "dynamics",
            f"Update {current.get('ticket') or case}",
            {"case": case, "fields": prepared["payload"]},
            preview={"fields": rows, "target": current.get("ticket") or case,
                     "url": current.get("url")},
            summary=", ".join(f"{r['name']}: {r['before']} → {r['after']}" for r in rows))
        ctx.drafts.append(actions.card(action))
        return {"content": f"Update prepared as change #{action.id}; waiting for approval.",
                "summary": "Waiting for approval"}

    return _guard(ctx, connector, run)


@tool("dynamics_resolve_case",
      "Propose resolving (closing) a Dynamics 365 case with a resolution "
      "summary. The user approves it first.",
      schema(["case", "resolution"], case=string_param("Case number (CAS-…)."),
             resolution=string_param("What fixed it — becomes the case resolution.")),
      mode="approval", label="Prepare a case resolution", activity="writing",
      available=_dynamics_on)
def dynamics_resolve_case(ctx: ToolContext, case: str = "", resolution: str = "", **_) -> dict:
    if not case.strip() or not resolution.strip():
        return {"content": "Resolving needs a case number and a resolution.",
                "summary": "Needs details"}
    action = actions.propose(
        ctx.db, "dynamics_resolve_case", "dynamics", f"Resolve {case}",
        {"case": case, "resolution": resolution},
        preview={"fields": [{"name": "Status", "before": "Active", "after": "Resolved"},
                            {"name": "Resolution", "before": "", "after": resolution}],
                 "target": case},
        summary=resolution[:80])
    ctx.drafts.append(actions.card(action))
    return {"content": f"Resolution prepared as change #{action.id}; waiting for approval.",
            "summary": "Waiting for approval"}


# ============================================================ RightAnswers
@tool("rightanswers_search",
      "Search the company's RightAnswers knowledge base for solutions and "
      "articles.",
      schema(["query"], query=string_param("Keywords, error codes or a short problem description.")),
      label="Search RightAnswers", activity="browsing", available=_rightanswers_on)
def rightanswers_search(ctx: ToolContext, query: str = "", **_) -> dict:
    connector = _rightanswers()

    def run():
        results = connector.search(query)
        if not results:
            return {"content": f"No RightAnswers articles matched “{query}”.",
                    "summary": "No matches"}
        lines = []
        for item in results:
            ref = ctx.cite({"title": item["title"], "kind": "rightanswers", "uri": item.get("url"),
                            "locator": f"Article {item.get('id') or ''}".strip(),
                            "excerpt": item.get("snippet") or ""}, "RA")
            lines.append(f"[{ref}] {item['title']} (id {item.get('id') or 'unknown'}): "
                         f"{item.get('snippet') or ''}")
        return {"content": "\n".join(lines) + "\nUse rightanswers_get_article to read one in full.",
                "summary": f"{len(results)} article(s)"}

    return _guard(ctx, connector, run)


@tool("rightanswers_get_article",
      "Read a RightAnswers article in full by its ID or address. Set "
      "remember=true to also add it to Cerebro's knowledge base.",
      schema(["article"], article=string_param("Article ID or full address."),
             remember={"type": "boolean", "description": "Also index it for later searches."}),
      label="Read RightAnswers article", activity="browsing", available=_rightanswers_on)
def rightanswers_get_article(ctx: ToolContext, article: str = "", remember: bool = False,
                             **_) -> dict:
    connector = _rightanswers()

    def run():
        item = connector.get_article(article)
        from app.services.source_service import SourceService

        SourceService(ctx.db).observe("rightanswers", item.get("url") or article, item["title"],
                                      uri=item.get("url"), content=item["body"],
                                      metadata={"article_id": item.get("id")})
        if remember:
            from app.services.rag_service import RAGService

            RAGService(ctx.db).upsert_document({
                "title": item["title"], "content": item["body"], "source": "RightAnswers",
                "url": item.get("url"), "tags": ["rightanswers"]})
        ref = ctx.cite({"title": item["title"], "kind": "rightanswers", "uri": item.get("url"),
                        "locator": f"Article {item.get('id')}", "excerpt": item["body"][:1000]},
                       "RA")
        return {"content": f"[{ref}] {item['title']}\n{item.get('meta') or ''}\n{item['body']}",
                "summary": item["title"]}

    return _guard(ctx, connector, run)


@tool("rightanswers_update_article",
      "Propose new content for an existing RightAnswers article. Give the "
      "complete new body (and title, if it changes). The user approves a "
      "before/after comparison first.",
      schema(["article", "body"], article=string_param("Article ID or address."),
             body=string_param("The full new article body."),
             title=string_param("New title, if it changes.")),
      mode="approval", label="Prepare an article update", activity="writing",
      available=_rightanswers_on)
def rightanswers_update_article(ctx: ToolContext, article: str = "", body: str = "",
                                title: str = "", **_) -> dict:
    connector = _rightanswers()
    if not body.strip():
        return {"content": "An article update needs the new body.", "summary": "Needs details"}

    def run():
        current = connector.get_article(article)
        fields = [{"name": "Body", "before": current["body"], "after": body}]
        if title and title != current["title"]:
            fields.insert(0, {"name": "Title", "before": current["title"], "after": title})
        action = actions.propose(
            ctx.db, "rightanswers_update_article", "rightanswers",
            f"Update article “{current['title']}”",
            {"article": current.get("id") or article, "body": body, "title": title or None},
            preview={"fields": fields, "target": current["title"], "url": current.get("url")},
            summary=f"{len(body)} characters, was {len(current['body'])}")
        ctx.drafts.append(actions.card(action))
        return {"content": f"Article update prepared as change #{action.id}; waiting for approval.",
                "summary": "Waiting for approval"}

    return _guard(ctx, connector, run)


@tool("rightanswers_create_article",
      "Propose a new RightAnswers article (for example, written up from a "
      "resolved case). The user approves it before it is created.",
      schema(["title", "body"], title=string_param("Article title."),
             body=string_param("Article body: symptoms, cause, resolution steps.")),
      mode="approval", label="Prepare a new article", activity="writing",
      available=_rightanswers_on)
def rightanswers_create_article(ctx: ToolContext, title: str = "", body: str = "", **_) -> dict:
    if not title.strip() or not body.strip():
        return {"content": "A new article needs a title and a body.", "summary": "Needs details"}
    action = actions.propose(
        ctx.db, "rightanswers_create_article", "rightanswers", f"Create article “{title}”",
        {"title": title, "body": body},
        preview={"fields": [{"name": "Title", "before": "", "after": title},
                            {"name": "Body", "before": "", "after": body}]},
        summary=f"New article, {len(body)} characters")
    ctx.drafts.append(actions.card(action))
    return {"content": f"New article prepared as change #{action.id}; waiting for approval.",
            "summary": "Waiting for approval"}


_PREFETCHED: Dict[str, float] = {}
PREFETCH_COOLDOWN_SECONDS = 300


def prefetch_dynamics_case(case: str) -> bool:
    """Read a Dynamics case in the background and keep it as a source.

    Called when the browser extension reports a case being opened. Skipped
    when Dynamics is off, and at most once every few minutes per case.
    Returns whether a fetch was started.
    """
    import threading
    import time

    connector = _dynamics()
    if not connector.enabled or not case:
        return False
    now = time.time()
    if now - _PREFETCHED.get(case, 0) < PREFETCH_COOLDOWN_SECONDS:
        return False
    _PREFETCHED[case] = now

    def work():
        from app.core import logger
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            record = connector.get_case(case)
            _remember_case(db, record, connector.case_text(record))
        except Exception as exc:  # noqa: BLE001 - background convenience only
            logger.info("browser", "Background case read skipped",
                        {"case": case, "reason": str(exc)[:200]})
        finally:
            db.close()

    threading.Thread(target=work, daemon=True, name="cerebro-case-prefetch").start()
    return True


# ================================================== approved-change executors
@actions.executor("dynamics_add_note")
def _run_add_note(db, args: dict) -> dict:
    return _dynamics().add_note(args["case"], args["text"], args.get("subject"))


@actions.executor("dynamics_update_case")
def _run_update_case(db, args: dict) -> dict:
    return _dynamics().update_case(args["case"], args["fields"])


@actions.executor("dynamics_resolve_case")
def _run_resolve_case(db, args: dict) -> dict:
    return _dynamics().resolve_case(args["case"], args["resolution"])


@actions.executor("rightanswers_update_article")
def _run_update_article(db, args: dict) -> dict:
    return _rightanswers().update_article(args["article"], body=args.get("body"),
                                          title=args.get("title"))


@actions.executor("rightanswers_create_article")
def _run_create_article(db, args: dict) -> dict:
    return _rightanswers().create_article(args["title"], args["body"])
