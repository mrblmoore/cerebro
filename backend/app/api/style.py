"""API for the writing voice and persona."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.services.style_service import StyleService

router = APIRouter(prefix="/api/style", tags=["style"])


class SampleIn(BaseModel):
    text: str
    channel: str = "email"


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return StyleService(db).status()


@router.post("/sample", dependencies=[Depends(require_local_origin)])
def add_sample(sample: SampleIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Feed Cerebro a piece of the user's own writing to learn from."""
    StyleService(db).add_sample(sample.text, channel=sample.channel)
    return StyleService(db).status()


@router.post("/learn", dependencies=[Depends(require_local_origin)])
def learn(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Recompute the writing voice from collected samples."""
    return StyleService(db).learn()
