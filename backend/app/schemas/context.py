from pydantic import BaseModel
from typing import Optional


class ContextStateResponse(BaseModel):
    crm_case: Optional[str]
    crm_system: Optional[str]
    customer: Optional[str]
    call_active: bool
    remote_session_active: bool
    active_application: Optional[str]
    active_url: Optional[str]
    
    class Config:
        from_attributes = True
