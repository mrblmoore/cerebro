from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Float
from sqlalchemy.sql import func
from app.core.database import Base


class AudioRecording(Base):
    __tablename__ = "audio_recordings"

    id = Column(Integer, primary_key=True, index=True)
    audio_path = Column(String, nullable=False)
    trigger_event_id = Column(Integer, nullable=True)
    source = Column(String, nullable=True)  # e.g., desktop_agent
    start_time = Column(DateTime, default=func.now())
    end_time = Column(DateTime, nullable=True)
    duration = Column(Float, nullable=True)
    status = Column(String, default="created")  # created, recording, uploaded, transcribed, error
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return f"<AudioRecording {self.id} {self.audio_path}>"


class Transcription(Base):
    __tablename__ = "transcriptions"

    id = Column(Integer, primary_key=True, index=True)
    audio_id = Column(Integer, ForeignKey("audio_recordings.id"), nullable=True)
    transcript_text = Column(Text, nullable=False)
    provider = Column(String, nullable=True)  # 'whisper', 'faster-whisper'
    confidence = Column(String, nullable=True)
    details = Column(Text, nullable=True)  # JSON blob with per-segment times
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Transcription {self.id} audio={self.audio_id}>"
