"""Ask tools for analysis: reviewing log files and comparing support cases."""

import os
import re
from typing import Dict, List

from app.services.agent.registry import ToolContext, schema, string_param, tool

MAX_LOG_BYTES = 20_000_000
_LEVEL = re.compile(r"\b(fatal|critical|error|exception|fail(?:ed|ure)?|warn(?:ing)?|timeout|denied)\b", re.I)
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{1,2}/\d{1,2}/\d{2,4}[ ,]+\d{1,2}:\d{2}:\d{2}")
_NOISE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\d+", re.I)


def summarise_log(text: str, focus: str = "", limit: int = 12) -> str:
    """Group repeated error/warning lines, with counts and first/last timestamps."""
    focus = (focus or "").strip().lower()
    groups: Dict[str, dict] = {}
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if not _LEVEL.search(line) and not (focus and focus in line.lower()):
            continue
        if focus and focus not in line.lower() and not _LEVEL.search(line):
            continue
        stamp = _STAMP.search(line)
        key = _NOISE.sub("#", _STAMP.sub("", line)).strip()[:200]
        group = groups.setdefault(key, {"count": 0, "first": None, "last": None,
                                        "line": number, "sample": line.strip()[:300]})
        group["count"] += 1
        if stamp:
            group["first"] = group["first"] or stamp.group(0)
            group["last"] = stamp.group(0)
    if not groups:
        return f"{len(lines)} lines read; no errors or warnings found" + (
            f" and nothing matching “{focus}”." if focus else ".")
    ranked = sorted(groups.values(), key=lambda g: -g["count"])[:limit]
    out = [f"{len(lines)} lines read; {sum(g['count'] for g in groups.values())} error/warning "
           f"lines in {len(groups)} distinct patterns. Most frequent:"]
    for g in ranked:
        span = f" · {g['first']} → {g['last']}" if g["first"] else ""
        out.append(f"- ×{g['count']}{span} · first at line {g['line']}: {g['sample']}")
    return "\n".join(out)


@tool("review_log",
      "Read a log file on this computer and summarise it: repeated errors and warnings "
      "grouped with counts and first/last timestamps. Use 'focus' to pick out a word or ID.",
      schema(["path"], path=string_param("Full path of the log file."),
             focus=string_param("Optional word, error code or ID to concentrate on.")),
      label="Review a log", activity="reading")
def review_log(ctx: ToolContext, path: str = "", focus: str = "", **_) -> dict:
    path = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"')))
    if not os.path.isfile(path):
        return {"content": f"No file at {path or '(no path given)'}.", "summary": "Not found"}
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > MAX_LOG_BYTES:
            handle.seek(size - MAX_LOG_BYTES)
        text = handle.read().decode("utf-8", errors="replace")
    return {"content": summarise_log(text, focus), "summary": os.path.basename(path)}


def _case_on(ctx: ToolContext) -> bool:
    from app.services.agent.integration_tools import _dynamics_on

    return _dynamics_on(ctx)


@tool("compare_cases",
      "Fetch two to four Dynamics 365 cases (by case number) and lay them side by side so "
      "differences, shared symptoms and likely common causes can be compared.",
      schema(["cases"], cases=string_param("Case numbers separated by commas or spaces.")),
      label="Compare cases", activity="browsing", available=_case_on)
def compare_cases(ctx: ToolContext, cases: str = "", **_) -> dict:
    from app.services.agent.integration_tools import _dynamics, _guard, _remember_case

    wanted: List[str] = list(dict.fromkeys(re.findall(r"[A-Za-z]{2,5}-\d[\w-]*", cases or "")))[:4]
    if len(wanted) < 2:
        return {"content": "Give at least two case numbers to compare.", "summary": "Need 2+ cases"}
    connector = _dynamics()
    blocks = []
    for number in wanted:
        def run(number=number):
            record = connector.get_case(number)
            text = connector.case_text(record)
            _remember_case(ctx.db, record, text)
            ref = ctx.cite({"title": f"{record['ticket']} — {record['title']}", "kind": "dynamics",
                            "uri": record.get("url"), "locator": "Dynamics 365",
                            "excerpt": text[:1000]}, "CRM")
            return {"content": f"[{ref}] {record['ticket']}\n{text[:3500]}", "summary": number}

        result = _guard(ctx, connector, run)
        blocks.append(result["content"])
        if result.get("summary") in ("Sign-in needed", "Not available"):
            break
    return {"content": "\n\n=====\n\n".join(blocks), "summary": f"{len(blocks)} case(s)"}
