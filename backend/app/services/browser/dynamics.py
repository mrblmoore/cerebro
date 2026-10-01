"""
Dynamics 365 (Customer Service) through the hidden browser.

Rather than clicking through forms, the connector calls Dataverse's own Web API
(``/api/data/v9.2``) **from inside the signed-in page**. The request carries
the browser's session cookies exactly as the Dynamics web app's own requests
do, so it needs no app registration, client secret or stored password — and
it is far less fragile than screen automation, because it does not depend on
form layouts that admins customise.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.services.browser import register
from app.services.browser.connector import BrowserConnector

API = "/api/data/v9.2"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json; charset=utf-8",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
    "Prefer": 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"',
}
FORMATTED = "@OData.Community.Display.V1.FormattedValue"
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TICKET_RE = re.compile(r"\bCAS-\d{3,}-[A-Z0-9]{4,}\b", re.IGNORECASE)

CASE_FIELDS = ("incidentid,ticketnumber,title,description,statecode,statuscode,"
               "prioritycode,severitycode,caseorigincode,createdon,modifiedon,"
               "_customerid_value,_ownerid_value")

#: Case fields Cerebro may change (with approval), and how to send each one.
#: Option-set fields accept the number or, case-insensitively, its label.
EDITABLE_FIELDS = {
    "title": "text", "description": "text",
    "prioritycode": "option", "severitycode": "option", "statuscode": "option",
}
OPTION_LABELS = {
    "prioritycode": {"high": 1, "normal": 2, "low": 3},
    "severitycode": {"default value": 1},
    "statuscode": {"in progress": 1, "on hold": 2, "waiting for details": 3, "researching": 4},
}
#: Resolution status codes for CloseIncident.
RESOLUTION_STATUS = {"problem solved": 5, "information provided": 1000}


class DynamicsError(RuntimeError):
    pass


def _odata_string(value: str) -> str:
    return (value or "").replace("'", "''")


def _formatted(record: dict, field: str) -> Any:
    return record.get(field + FORMATTED, record.get(field))


class DynamicsConnector(BrowserConnector):
    name = "dynamics"
    label = "Dynamics 365"
    enabled_setting = "DYNAMICS_ENABLED"
    url_setting = "DYNAMICS_URL"
    default_selectors: Dict[str, Any] = {}

    def sign_in_url(self) -> str:
        return self.url("/main.aspx")

    def is_signed_in(self, page) -> bool:
        return self.on_own_site(page.url) and not self.looks_like_login(page.url)

    def record_url(self, incident_id: str) -> str:
        return self.url(f"/main.aspx?pagetype=entityrecord&etn=incident&id={incident_id}")

    # ------------------------------------------------------------ plumbing
    def _api(self, page, path: str, method: str = "GET", body: Any = None) -> Dict[str, Any]:
        # The WhoAmI endpoint is a tiny same-origin page: landing there gives
        # the in-page fetch the right origin without loading the whole app.
        if not self.on_own_site(page.url) or self.looks_like_login(page.url):
            self.goto(page, f"{API}/WhoAmI")
        result = self.fetch(page, f"{API}/{path.lstrip('/')}", method=method,
                            body=body, headers=HEADERS)
        if not result.get("ok"):
            error = ((result.get("json") or {}).get("error") or {}).get("message") \
                or (result.get("text") or "")[:300] or f"HTTP {result.get('status')}"
            raise DynamicsError(f"Dynamics refused the request: {error}")
        return result

    def _resolve_id(self, page, case: str) -> str:
        """A ticket number or GUID → the incident GUID."""
        case = (case or "").strip()
        if GUID_RE.match(case.strip("{}")):
            return case.strip("{}")
        ticket = TICKET_RE.search(case)
        number = ticket.group(0).upper() if ticket else case
        data = self._api(page, f"incidents?$select=incidentid&$top=1&$filter="
                               f"ticketnumber eq '{quote(_odata_string(number))}'")["json"] or {}
        rows = data.get("value") or []
        if not rows:
            raise DynamicsError(f"No Dynamics case found with number {number}.")
        return rows[0]["incidentid"]

    @staticmethod
    def _case_summary(row: dict) -> Dict[str, Any]:
        return {
            "id": row.get("incidentid"),
            "ticket": row.get("ticketnumber"),
            "title": row.get("title"),
            "customer": _formatted(row, "_customerid_value"),
            "owner": _formatted(row, "_ownerid_value"),
            "status": _formatted(row, "statuscode"),
            "state": _formatted(row, "statecode"),
            "priority": _formatted(row, "prioritycode"),
            "modified": row.get("modifiedon"),
            "created": row.get("createdon"),
        }

    # --------------------------------------------------------------- reads
    def search_cases(self, query: str = "", status: str = "", limit: int = 10) -> List[dict]:
        filters = []
        query = (query or "").strip()
        ticket = TICKET_RE.search(query)
        if ticket:
            filters.append(f"ticketnumber eq '{_odata_string(ticket.group(0).upper())}'")
        elif query:
            q = _odata_string(query)
            filters.append(f"(contains(title,'{q}') or contains(ticketnumber,'{q}') or "
                           f"contains(description,'{q}'))")
        lowered = (status or "").lower()
        if lowered in ("open", "active"):
            filters.append("statecode eq 0")
        elif lowered in ("resolved", "closed"):
            filters.append("statecode eq 1")
        path = (f"incidents?$select={CASE_FIELDS}&$orderby=modifiedon desc"
                f"&$top={max(1, min(int(limit or 10), 50))}")
        if filters:
            path += "&$filter=" + quote(" and ".join(filters), safe="=,'()$ ")

        def work(page):
            data = self._api(page, path)["json"] or {}
            return [self._case_summary(row) for row in data.get("value") or []]

        return self.run(work, f"Searching {self.label} cases")

    def get_case(self, case: str) -> Dict[str, Any]:
        def work(page):
            incident_id = self._resolve_id(page, case)
            row = self._api(page, f"incidents({incident_id})?$select={CASE_FIELDS}")["json"] or {}
            notes = self._api(
                page, f"annotations?$select=subject,notetext,createdon,_createdby_value"
                      f"&$filter=_objectid_value eq {incident_id}&$orderby=createdon desc&$top=15"
            )["json"] or {}
            activities = self._api(
                page, f"activitypointers?$select=subject,activitytypecode,description,createdon"
                      f"&$filter=_regardingobjectid_value eq {incident_id}"
                      f"&$orderby=createdon desc&$top=15")["json"] or {}
            return {
                **self._case_summary(row),
                "description": row.get("description"),
                "url": self.record_url(incident_id),
                "raw": {k: row.get(k) for k in EDITABLE_FIELDS},
                "notes": [{"subject": n.get("subject"), "text": n.get("notetext"),
                           "created": n.get("createdon"),
                           "by": _formatted(n, "_createdby_value")}
                          for n in notes.get("value") or []],
                "activities": [{"subject": a.get("subject"),
                                "type": _formatted(a, "activitytypecode"),
                                "description": (a.get("description") or "")[:800],
                                "created": a.get("createdon")}
                               for a in activities.get("value") or []],
            }

        return self.run(work, f"Reading {self.label} case {case}")

    # -------------------------------------------------------------- writes
    # Called only by approved AgentActions (see app.services.agent).
    def add_note(self, case: str, text: str, subject: str = None) -> Dict[str, Any]:
        def work(page):
            incident_id = self._resolve_id(page, case)
            body = {"subject": subject or "Note from Cerebro", "notetext": text,
                    "objectid_incident@odata.bind": f"/incidents({incident_id})"}
            result = self._api(page, "annotations", method="POST", body=body)
            return {"incident_id": incident_id, "note": result.get("entity_id"),
                    "detail": f"note added to {case}", "url": self.record_url(incident_id)}

        return self.run(work, f"Adding a note to {case}")

    def update_case(self, case: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.normalise_fields(fields)

        def work(page):
            incident_id = self._resolve_id(page, case)
            self._api(page, f"incidents({incident_id})", method="PATCH", body=payload)
            return {"incident_id": incident_id, "fields": list(payload),
                    "detail": f"{case} updated ({', '.join(payload)})",
                    "url": self.record_url(incident_id)}

        return self.run(work, f"Updating {case}")

    def resolve_case(self, case: str, resolution: str,
                     status: str = "problem solved") -> Dict[str, Any]:
        code = RESOLUTION_STATUS.get((status or "").lower(), 5)

        def work(page):
            incident_id = self._resolve_id(page, case)
            self._api(page, "CloseIncident", method="POST", body={
                "IncidentResolution": {
                    "subject": (resolution or "Resolved")[:200],
                    "description": resolution,
                    "incidentid@odata.bind": f"/incidents({incident_id})",
                },
                "Status": code,
            })
            return {"incident_id": incident_id, "detail": f"{case} resolved",
                    "url": self.record_url(incident_id)}

        return self.run(work, f"Resolving {case}")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def normalise_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a requested change and convert labels to option values."""
        payload = {}
        for key, value in (fields or {}).items():
            name = str(key).lower().replace(" ", "")
            name = {"priority": "prioritycode", "severity": "severitycode",
                    "status": "statuscode", "subject": "title"}.get(name, name)
            kind = EDITABLE_FIELDS.get(name)
            if kind is None:
                raise DynamicsError(
                    f"Cerebro does not change the “{key}” field. It can change: "
                    + ", ".join(EDITABLE_FIELDS) + ".")
            if kind == "option":
                if isinstance(value, str) and not value.strip().isdigit():
                    mapped = OPTION_LABELS.get(name, {}).get(value.strip().lower())
                    if mapped is None:
                        raise DynamicsError(f"Unknown value “{value}” for {name}.")
                    value = mapped
                value = int(value)
            else:
                value = str(value)
            payload[name] = value
        if not payload:
            raise DynamicsError("No fields to change.")
        return payload

    @staticmethod
    def case_text(case: Dict[str, Any]) -> str:
        """A readable rendering of a case, for the model and for sources."""
        lines = [f"{case.get('ticket')}: {case.get('title')}",
                 f"Customer: {case.get('customer') or 'unknown'} · Status: {case.get('status')}"
                 f" · Priority: {case.get('priority')} · Owner: {case.get('owner')}",
                 f"Created {case.get('created')} · Modified {case.get('modified')}"]
        if case.get("description"):
            lines.append(f"Description:\n{case['description']}")
        if case.get("notes"):
            lines.append("Notes (newest first):")
            lines += [f"- {n.get('created')} {n.get('by') or ''}: {n.get('subject') or ''} — "
                      f"{(n.get('text') or '')[:600]}" for n in case["notes"]]
        if case.get("activities"):
            lines.append("Activities (newest first):")
            lines += [f"- {a.get('created')} [{a.get('type')}] {a.get('subject') or ''}"
                      f"{': ' + a['description'][:300] if a.get('description') else ''}"
                      for a in case["activities"]]
        return "\n".join(lines)


connector: Optional[DynamicsConnector] = register(DynamicsConnector())
