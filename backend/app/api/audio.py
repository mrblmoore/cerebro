"""API routes for audio recordings and transcriptions."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.audio import AudioRecording, Transcription
from typing import List
from app.core import logger

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.get("/recordings")
async def list_recordings(limit: int = 50, db: Session = Depends(get_db)):
    logger.info('api.audio', 'Listing recordings', {'limit': limit})
    recordings = db.query(AudioRecording).order_by(AudioRecording.start_time.desc()).limit(limit).all()
    results = []
    for r in recordings:
        results.append({
            "id": r.id,
            "audio_path": r.audio_path,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "duration": r.duration,
            "status": r.status,
            "source": r.source
        })
    return {"count": len(results), "recordings": results}


@router.get("/transcriptions")
async def list_transcriptions(limit: int = 50, db: Session = Depends(get_db)):
    logger.info('api.audio', 'Listing transcriptions', {'limit': limit})
    trs = db.query(Transcription).order_by(Transcription.created_at.desc()).limit(limit).all()
    results = []
    for t in trs:
        results.append({
            "id": t.id,
            "audio_id": t.audio_id,
            "transcript_text": t.transcript_text,
            "provider": t.provider,
            "created_at": t.created_at
        })
    return {"count": len(results), "transcriptions": results}


@router.get("/transcriptions/{transcription_id}")
async def get_transcription(transcription_id: int, db: Session = Depends(get_db)):
    logger.info('api.audio', 'Get transcription', {'transcription_id': transcription_id})
    tr = db.query(Transcription).filter(Transcription.id == transcription_id).first()
    if not tr:
        logger.warn('api.audio', 'Transcription not found', {'transcription_id': transcription_id})
        raise HTTPException(status_code=404, detail="Transcription not found")
    return {
        "id": tr.id,
        "audio_id": tr.audio_id,
        "transcript_text": tr.transcript_text,
        "provider": tr.provider,
        "details": tr.details,
        "created_at": tr.created_at
    }
