from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class EventCreate(BaseModel):
    event_type: str
    case_id: Optional[str] = None
    source: str
    data: Dict[str, Any]
    screenshot_path: Optional[str] = None
    ocr_text: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    event_type: str
    case_id: Optional[str]
    source: str
    data: Dict[str, Any]
    created_at: datetime
    
    class Config:
        from_attributes = True
