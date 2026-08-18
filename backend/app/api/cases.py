"""API routes for support cases."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.case import Case
from app.schemas.case import CaseCreate, CaseResponse
from app.services.llm_service import LLMService

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post("/", response_model=CaseResponse)
async def create_case(case: CaseCreate, db: Session = Depends(get_db)):
    """Create a case, generating an AI summary when a provider is configured."""
    existing = db.query(Case).filter(Case.case_id == case.case_id).first()
    if existing:
        return existing

    llm = LLMService()
    payload = case.model_dump()

    db_case = Case(**payload)
    if llm.enabled:
        db_case.ai_summary = llm.generate_case_summary(payload)
        db_case.troubleshooting_steps = llm.generate_troubleshooting_steps(payload, {})

    db.add(db_case)
    db.commit()
    db.refresh(db_case)
    return db_case


@router.get("/", response_model=List[CaseResponse])
async def list_cases(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Case)
    if status:
        query = query.filter(Case.status == status)
    return query.order_by(Case.updated_at.desc()).limit(limit).all()


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(case_id: str, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    return case


@router.post("/{case_id}/summarise", response_model=CaseResponse)
async def summarise_case(case_id: str, db: Session = Depends(get_db)):
    """(Re)generate the AI summary for a case. Backs the widget's Summarise action."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    llm = LLMService()
    if not llm.enabled:
        raise HTTPException(
            status_code=409,
            detail="No AI provider configured. Set one up in Settings → AI Provider.",
        )

    payload = {
        "customer": case.customer,
        "title": case.title,
        "error_code": case.error_code,
        "application": case.application,
    }
    case.ai_summary = llm.generate_case_summary(payload)
    case.troubleshooting_steps = llm.generate_troubleshooting_steps(payload, {})
    db.commit()
    db.refresh(case)
    return case
