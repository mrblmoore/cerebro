"""
API for memory — inspecting, curating and teaching the second brain.

Reading memories requires a local origin, since they are distilled from
everything Cerebro has watched.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.models.memory import Memory
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryIn(BaseModel):
    title: str
    content: str
    memory_type: str = "fact"
    case_id: Optional[str] = None
    customer: Optional[str] = None
    tags: Optional[List[str]] = None
    pinned: bool = False


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return MemoryService(db).status()


@router.get("", dependencies=[Depends(require_local_origin)])
def list_memories(limit: int = Query(50, ge=1, le=200), memory_type: Optional[str] = None,
                  case_id: Optional[str] = None,
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    memories = MemoryService(db).list_memories(limit=limit, memory_type=memory_type,
                                               case_id=case_id)
    return {"count": len(memories), "memories": [m.to_dict() for m in memories]}


@router.get("/recall", dependencies=[Depends(require_local_origin)])
def recall(query: str = Query(..., min_length=2), limit: int = Query(6, ge=1, le=20),
           case_id: Optional[str] = None, customer: Optional[str] = None,
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What Cerebro would recall for a given task — useful for debugging."""
    results = MemoryService(db).recall(query, limit=limit, case_id=case_id,
                                       customer=customer)
    return {"query": query, "count": len(results), "memories": results}


@router.post("", dependencies=[Depends(require_local_origin)])
def teach(memory: MemoryIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Teach Cerebro something directly."""
    try:
        record = MemoryService(db).remember(
            title=memory.title, content=memory.content, memory_type=memory.memory_type,
            case_id=memory.case_id, customer=memory.customer, tags=memory.tags,
            source="manual", confidence=0.8, pinned=memory.pinned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_dict()


@router.post("/distil", dependencies=[Depends(require_local_origin)])
def distil(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Distil pending captured activity into memories now."""
    return MemoryService(db).distil_activity()


@router.delete("/{memory_id}", dependencies=[Depends(require_local_origin)])
def forget(memory_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    memory = db.query(Memory).get(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    MemoryService(db).forget(memory)
    return {"ok": True, "forgotten": memory_id}


@router.post("/{memory_id}/pin", dependencies=[Depends(require_local_origin)])
def pin(memory_id: int, pinned: bool = True, db: Session = Depends(get_db)) -> Dict[str, Any]:
    memory = db.query(Memory).get(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory.pinned = pinned
    db.commit()
    return memory.to_dict()
