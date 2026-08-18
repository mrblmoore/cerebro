"""API routes for cases."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core import get_db
from app.schemas.case import CaseCreate, CaseResponse
from app.services.llm_service import LLMService
from app.models.case import Case
from typing import List

router = APIRouter(prefix="/api/cases", tags=["cases"])

llm_service = LLMService()


@router.post("/", response_model=CaseResponse)
async def create_case(
    case: CaseCreate,
    db: Session = Depends(get_db)
):
    """Create a new case."""
    # Check if case already exists
    existing = db.query(Case).filter(Case.case_id == case.case_id).first()
    if existing:
        return existing
    
    # Generate AI summary
    case_data = case.dict()
    ai_summary = llm_service.generate_case_summary(case_data)
    troubleshooting = llm_service.generate_troubleshooting_steps(case_data, {})
    
    db_case = Case(
        **case.dict(),
        ai_summary=ai_summary,
        troubleshooting_steps=troubleshooting
    )
    db.add(db_case)
    db.commit()
    db.refresh(db_case)
    return db_case


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(case_id: str, db: Session = Depends(get_db)):
    """Get a specific case."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/", response_model=List[CaseResponse])
async def list_cases(
    status: str = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List cases."""
    query = db.query(Case)
    if status:
        query = query.filter(Case.status == status)
    return query.order_by(Case.updated_at.desc()).limit(limit).all()
