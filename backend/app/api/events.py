"""API routes for events."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core import get_db
from app.schemas.event import EventCreate, EventResponse
from app.services.context_engine import ContextEngine
from typing import List
from app.core import logger

router = APIRouter(prefix="/api/events", tags=["events"])


@router.post("/", response_model=dict)
async def create_event(
    event: EventCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new event and update context.
    Events trigger context updates and recommendations.
    """
    # Log incoming event payload
    logger.info('api.events', 'Incoming event POST', {'event_type': event.event_type, 'source': event.source, 'case_id': event.case_id})
    engine = ContextEngine(db)
    result = engine.process_event(event)
    logger.info('api.events', 'Event processed', {'event_id': result.get('event_id'), 'recommendations': result.get('recommendations')})
    return result


@router.get("/", response_model=List[EventResponse])
async def list_events(
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get recent events."""
    from app.models.event import Event
    events = db.query(Event).order_by(Event.created_at.desc()).limit(limit).all()
    return events


@router.get("/latest")
async def get_latest_event(db: Session = Depends(get_db)):
    """Get the most recent event."""
    from app.models.event import Event
    event = db.query(Event).order_by(Event.created_at.desc()).first()
    if not event:
        raise HTTPException(status_code=404, detail="No events found")
    return {
        "id": event.id,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "data": event.data
    }
