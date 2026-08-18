from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ContextStateResponse(BaseModel):
    """The live operational context, polled by the widget and dashboard."""

    crm_case: Optional[str] = None
    crm_system: Optional[str] = None
    customer: Optional[str] = None
    call_active: bool = False
    remote_session_active: bool = False
    remote_host: Optional[str] = None
    active_application: Optional[str] = None
    active_url: Optional[str] = None
    window_title: Optional[str] = None
    last_suggestion: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
