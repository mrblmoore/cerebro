from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CaseCreate(BaseModel):
    case_id: str
    system: str
    customer: str
    title: str
    description: Optional[str] = None
    error_code: Optional[str] = None
    application: Optional[str] = None


class CaseResponse(BaseModel):
    id: int
    case_id: str
    system: str
    customer: str
    title: str
    status: str
    ai_summary: Optional[str]
    troubleshooting_steps: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
