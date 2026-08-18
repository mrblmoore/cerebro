"""
Event detection — rules that turn a window title, process name or URL into a
Cerebro event.

Used by the desktop agent and available to anything else that wants to classify
raw desktop activity the same way Cerebro does.
"""

import re
from typing import Any, Dict, Optional, Tuple

Detection = Optional[Tuple[str, Dict[str, Any]]]

# Hostnames legitimately contain hyphens and dots, so a plain \w+ truncates them.
HOSTNAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"

REMOTE_PROCESSES = ("bomgar", "mstsc", "teamviewer", "anydesk", "vncviewer")
CALL_PROCESSES = ("teams", "zoom", "webex", "slack")


class EventDetector:
    """Detects meaningful events from window, URL and process information."""

    @staticmethod
    def detect_crm_event(url: str, title: str) -> Detection:
        """Detect CRM case pages from a URL and window title."""
        url = url or ""
        title = title or ""

        if "salesforce.com" in url or "force.com" in url:
            match = (re.search(r"/lightning/r/Case/([a-zA-Z0-9]{15,18})", url)
                     or re.search(r"/case/([a-zA-Z0-9]+)", url, re.IGNORECASE))
            if match:
                return "CRM_CASE_OPENED", {
                    "system": "Salesforce",
                    "case_id": match.group(1),
                    "customer": EventDetector._customer_from_title(title),
                    "url": url,
                }

        if "dynamics.com" in url:
            # Dynamics addresses records by GUID; the readable ticket number
            # (CAS-01234-ABCDEF) appears only in the page title.
            entity = re.search(r"[?&]etn=([a-z_]+)", url, re.IGNORECASE)
            if entity is None or entity.group(1).lower() in ("incident", "case"):
                ticket = re.search(r"\b(CAS-\d{4,}-[A-Z0-9]{5,})\b", title, re.IGNORECASE)
                guid = re.search(
                    r"[?&]id=%?7?B?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
                    r"-[0-9a-f]{4}-[0-9a-f]{12})", url, re.IGNORECASE)
                identifier = ticket.group(1) if ticket else (guid.group(1) if guid else None)
                if identifier:
                    return "CRM_CASE_OPENED", {
                        "system": "Dynamics 365",
                        "case_id": identifier.upper(),
                        "customer": EventDetector._customer_from_dynamics_title(title),
                        "url": url,
                    }

        if "service-now.com" in url:
            match = (re.search(r"number(?:%3D|=)([A-Z]{2,5}\d{5,})", url, re.IGNORECASE)
                     or re.search(r"/((?:INC|CS|RITM|SCTASK|CHG)\d{5,})", url, re.IGNORECASE))
            if match:
                return "CRM_CASE_OPENED", {
                    "system": "ServiceNow",
                    "case_id": match.group(1).upper(),
                    "customer": EventDetector._customer_from_title(title),
                    "url": url,
                }

        if "zendesk.com" in url:
            match = re.search(r"/agent/tickets/(\d+)", url)
            if match:
                return "CRM_CASE_OPENED", {
                    "system": "Zendesk",
                    "case_id": match.group(1),
                    "customer": EventDetector._customer_from_title(title),
                    "url": url,
                }

        return None

    @staticmethod
    def _customer_from_dynamics_title(title: str) -> Optional[str]:
        """
        Dynamics titles read "Case: CAS-0123-ABCDEF - Contoso Ltd - Dynamics 365",
        so the customer sits between the record and the product, separated by
        dashes rather than the pipes every other CRM uses.
        """
        parts = [part.strip() for part in re.split(r"\s+-\s+", title or "") if part.strip()]
        if len(parts) >= 3:
            return parts[-2]
        return EventDetector._customer_from_title(title)

    @staticmethod
    def _customer_from_title(title: str) -> Optional[str]:
        """
        CRM titles are pipe-separated, e.g. ``Case 00001234 | Contoso | Salesforce``,
        where the customer sits between the record and the product name.

        A two-part title is just ``record | product`` and carries no customer —
        guessing there would store the case number as the customer name.
        """
        parts = [part.strip() for part in (title or "").split("|") if part.strip()]
        return parts[-2] if len(parts) >= 3 else None

    @staticmethod
    def detect_remote_session_event(process_name: str, window_title: str) -> Detection:
        """Detect remote support session state (Bomgar, RDP, TeamViewer, …)."""
        process = (process_name or "").lower()
        title = window_title or ""
        lowered = title.lower()

        is_remote_tool = (any(name in process for name in REMOTE_PROCESSES)
                          or "remote support" in lowered
                          or "remote desktop" in lowered)
        if not is_remote_tool:
            return None

        match = re.search(rf"connected to\s+({HOSTNAME})", title, re.IGNORECASE)
        host = match.group(1) if match else None

        if "disconnect" in lowered or "session ended" in lowered:
            return "REMOTE_SESSION_DISCONNECTED", {"host": host}
        if "connected" in lowered or host:
            return "REMOTE_SESSION_CONNECTED", {"host": host}
        return None

    @staticmethod
    def detect_call_event(process_name: str, window_title: str) -> Detection:
        """Detect call state from a conferencing app's window title."""
        process = (process_name or "").lower()
        lowered = (window_title or "").lower()

        if not any(name in process for name in CALL_PROCESSES):
            return None

        if any(word in lowered for word in ("call ended", "meeting ended", "call finished")):
            return "CALL_ENDED", {"application": process_name, "title": window_title}
        if any(word in lowered for word in ("call", "meeting", "huddle")):
            return "CALL_STARTED", {"application": process_name, "title": window_title}
        return None

    # Kept for backwards compatibility with pre-0.2 callers.
    detect_teams_event = detect_call_event

    @staticmethod
    def detect_application_change(process_name: str, url: str,
                                  title: str) -> Tuple[str, Dict[str, Any]]:
        """Generic application/window change — the catch-all event."""
        return "APPLICATION_CHANGED", {
            "application": process_name,
            "url": url,
            "title": title,
        }
