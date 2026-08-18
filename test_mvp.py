"""
Test script for Cerebrus MVP
Tests core functionality without external dependencies.
"""

import json
import asyncio
from app.services.context_engine import ContextEngine
from app.services.event_detector import EventDetector
from app.schemas.event import EventCreate
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base

# Use SQLite for testing
TEST_DATABASE_URL = "sqlite:///./cerebrus_test.db"
engine = create_engine(
    TEST_DATABASE_URL, connect_args={"check_same_thread": False}
)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_context_engine():
    """Test context engine state transitions."""
    db = TestingSessionLocal()
    engine = ContextEngine(db)
    
    # Initialize context
    context = engine.init_context()
    assert context is not None
    print("✓ Context initialization")
    
    # Simulate case opening
    event = EventCreate(
        event_type="CRM_CASE_OPENED",
        source="test",
        data={
            "system": "Salesforce",
            "case_id": "12345",
            "customer": "Acme Corp"
        }
    )
    result = engine.process_event(event)
    assert result["context"]["crm_case"] == "12345"
    assert result["context"]["customer"] == "Acme Corp"
    print("✓ Case opened event")
    
    # Simulate call starting
    event = EventCreate(
        event_type="CALL_STARTED",
        source="test",
        data={"application": "Teams"}
    )
    result = engine.process_event(event)
    assert result["context"]["call_active"] == True
    print("✓ Call started event")
    
    # Simulate remote session
    event = EventCreate(
        event_type="REMOTE_SESSION_CONNECTED",
        source="test",
        data={"host": "SERVER-01"}
    )
    result = engine.process_event(event)
    assert result["context"]["remote_session_active"] == True
    print("✓ Remote session event")
    
    db.close()


def test_event_detector():
    """Test event detection rules."""
    
    # Test Salesforce detection
    event_type, data = EventDetector.detect_crm_event(
        "https://company.lightning.force.com/case/500abc123",
        "Case #12345 - Outlook Issue | John Doe"
    ) or (None, None)
    
    assert event_type == "CRM_CASE_OPENED"
    assert data["system"] == "Salesforce"
    assert data["case_id"] == "500abc123"
    print("✓ Salesforce detection")
    
    # Test Bomgar detection
    event_type, data = EventDetector.detect_remote_session_event(
        "bomgar-rep.exe",
        "BeyondTrust Remote Support - Connected to SERVER-01"
    ) or (None, None)
    
    assert event_type == "REMOTE_SESSION_CONNECTED"
    assert data["host"] == "SERVER-01"
    print("✓ Bomgar detection")


def test_event_flow():
    """Test complete event flow."""
    db = TestingSessionLocal()
    engine = ContextEngine(db)
    
    # Initialize
    engine.init_context()
    
    # Simulate a complete support scenario
    events = [
        {
            "type": "CRM_CASE_OPENED",
            "data": {"system": "Salesforce", "case_id": "500abc", "customer": "Contoso"}
        },
        {
            "type": "CALL_STARTED",
            "data": {"application": "Teams"}
        },
        {
            "type": "REMOTE_SESSION_CONNECTED",
            "data": {"host": "CONTOSO-PC"}
        },
        {
            "type": "CALL_ENDED",
            "data": {}
        }
    ]
    
    for event_data in events:
        event = EventCreate(
            event_type=event_data["type"],
            source="test",
            data=event_data["data"]
        )
        result = engine.process_event(event)
        print(f"✓ {event_data['type']}")
    
    # Verify final state
    context = engine.get_current_context()
    assert context.crm_case == "500abc"
    assert context.customer == "Contoso"
    assert context.call_active == False  # Call ended
    assert context.remote_session_active == True  # Still connected
    
    print("✓ Complete event flow")
    
    db.close()


if __name__ == "__main__":
    print("🧪 Running Cerebrus MVP tests...\n")
    
    test_event_detector()
    print()
    test_context_engine()
    print()
    test_event_flow()
    
    print("\n✅ All tests passed!")
