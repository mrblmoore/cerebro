"""
Event Detection - Rule-based detection of important application and context changes.
"""

from typing import Dict, Any, Optional, Tuple
import re


class EventDetector:
    """Detects meaningful events from window/URL/application changes."""
    
    @staticmethod
    def detect_crm_event(url: str, title: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Detect CRM-related events from URL and window title."""
        
        # Salesforce detection
        if "salesforce.com" in url or "lightning.force.com" in url:
            # Extract case ID from URL
            case_match = re.search(r'/case/([a-zA-Z0-9]+)', url)
            if case_match:
                case_id = case_match.group(1)
                # Extract customer name from title if available
                customer = title.split("|")[0].strip() if "|" in title else "Unknown"
                
                return ("CRM_CASE_OPENED", {
                    "system": "Salesforce",
                    "case_id": case_id,
                    "customer": customer,
                    "url": url
                })
        
        return None
    
    @staticmethod
    def detect_remote_session_event(process_name: str, window_title: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Detect remote session events (Bomgar, RDP, etc)."""
        
        if "bomgar" in process_name.lower() or "remote support" in window_title.lower():
            # Extract host name if available
            host_match = re.search(r'Connected to\s+(\w+)', window_title)
            host = host_match.group(1) if host_match else "Unknown"
            
            if "connected" in window_title.lower():
                return ("REMOTE_SESSION_CONNECTED", {"host": host})
            else:
                return ("REMOTE_SESSION_DISCONNECTED", {"host": host})
        
        return None
    
    @staticmethod
    def detect_teams_event(process_name: str, window_title: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Detect Teams call state changes."""
        
        if "teams" in process_name.lower():
            if "call" in window_title.lower() or "meeting" in window_title.lower():
                return ("CALL_STARTED", {"application": "Teams", "title": window_title})
        
        return None
    
    @staticmethod
    def detect_application_change(process_name: str, url: str, title: str) -> Tuple[str, Dict[str, Any]]:
        """Generic application/window change detection."""
        return ("APPLICATION_CHANGED", {
            "application": process_name,
            "url": url,
            "title": title
        })
