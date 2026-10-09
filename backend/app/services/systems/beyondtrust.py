"""BeyondTrust Remote Support, through its Reporting API (read-only)."""

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Dict, List

from app.core.config import settings
from app.services.systems.base import ApiConnector, SystemCallError


def _text(element) -> str:
    return (element.text or "").strip()


def flatten(element) -> Dict[str, Any]:
    """An XML element as a flat dict: attributes plus child text, repeated
    children (e.g. several representatives) collected into lists."""
    out: Dict[str, Any] = dict(element.attrib)
    for child in element:
        key = child.tag
        value: Any = flatten(child) if (len(child) or child.attrib) else _text(child)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    return out


def parse_sessions(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SystemCallError(f"BeyondTrust sent a report Cerebro couldn't read: {exc}") from exc
    return [flatten(node) for node in root.iter("session")]


class BeyondTrust(ApiConnector):
    name = "beyondtrust"
    label = "BeyondTrust"
    enabled_setting = "BEYONDTRUST_ENABLED"
    required = (("BEYONDTRUST_URL", "site address"),
                ("BEYONDTRUST_CLIENT_ID", "API client ID"),
                ("BEYONDTRUST_CLIENT_SECRET", "API client secret"))

    def _host(self) -> str:
        host = (settings.BEYONDTRUST_URL or "").strip().rstrip("/")
        if host and not host.lower().startswith(("http://", "https://")):
            host = "https://" + host
        return host

    def api_base(self) -> str:
        return self._host()

    def token_url(self) -> str:
        return f"{self._host()}/oauth2/token"

    def credentials(self):
        return settings.BEYONDTRUST_CLIENT_ID, settings.BEYONDTRUST_CLIENT_SECRET

    def display_address(self) -> str:
        return self._host()

    def verify(self) -> str:
        self.bearer(force=True)
        self.sessions(days=1, limit=1)
        return f"Connected to {self._host()} (Reporting API reachable)."

    # ----------------------------------------------------------- reads
    def _report(self, **params) -> str:
        return self.request("GET", "/api/reporting", params=params,
                            headers={"Accept": "application/xml"}).text

    def sessions(self, days: int = 1, query: str = "", limit: int = 25) -> List[Dict[str, Any]]:
        """Support sessions from the last ``days`` days, newest first,
        optionally narrowed to those mentioning ``query``."""
        days = max(1, min(int(days or 1), 31))
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        text = self._report(generate_report="SupportSession", start_date=start, duration=0)
        rows = parse_sessions(text)
        needle = (query or "").strip().lower()
        if needle:
            rows = [row for row in rows if needle in str(row).lower()]
        rows.sort(key=lambda row: str(row.get("start_time") or ""), reverse=True)
        return rows[:max(1, int(limit))]

    def session(self, lsid: str) -> Dict[str, Any]:
        rows = parse_sessions(self._report(generate_report="SupportSession", lsid=lsid))
        if not rows:
            raise SystemCallError(f"BeyondTrust has no session {lsid}.")
        return rows[0]


def summary(row: Dict[str, Any]) -> Dict[str, Any]:
    """The fields worth showing, whatever the report calls them."""
    pick = lambda *keys: next((row[k] for k in keys if row.get(k)), "")
    return {"id": pick("lsid", "session_id"), "start": pick("start_time"),
            "end": pick("end_time"), "duration": pick("duration"),
            "customer": pick("customer_name", "primary_customer_name"),
            "company": pick("customer_company", "primary_customer_company"),
            "representative": pick("representative_name", "primary_rep_name"),
            "external_key": pick("external_key"),
            "type": pick("session_type", "type")}


connector = BeyondTrust()
