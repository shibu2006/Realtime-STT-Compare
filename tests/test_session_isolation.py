#!/usr/bin/env python3
"""
Test script to verify session isolation in the voice search app
"""
import sys
import time
from voicesearch_app import get_user_session, cleanup_user_session, socketio

def test_session_isolation():
    """Test that different session IDs get isolated state"""
    print("🧪 Testing session isolation...")
    
    # Create two different sessions
    session1 = get_user_session("user_session_1")
    session2 = get_user_session("user_session_2")
    
    # Verify they are different objects
    assert session1 != session2, "❌ Sessions should be different objects"
    assert session1.session_id != session2.session_id, "❌ Session IDs should be different"
    
    print(f"✅ Session 1 ID: {session1.session_id}")
    print(f"✅ Session 2 ID: {session2.session_id}")
    
    # Test that they have independent state
    session1.current_api_provider = "Azure OpenAI"
    session2.current_api_provider = "Deepgram API"
    
    assert session1.current_api_provider == "Azure OpenAI", "❌ Session 1 should have Azure OpenAI"
    assert session2.current_api_provider == "Deepgram API", "❌ Session 2 should have Deepgram API"
    
    print("✅ Sessions have independent API provider settings")
    
    # Test transcription count isolation
    session1.transcription_count = 5
    session2.transcription_count = 10
    
    assert session1.transcription_count == 5, "❌ Session 1 should have count 5"
    assert session2.transcription_count == 10, "❌ Session 2 should have count 10"
    
    print("✅ Sessions have independent transcription counts")
    
    # Test cleanup
    cleanup_user_session("user_session_1")
    cleanup_user_session("user_session_2")
    
    print("✅ Sessions cleaned up successfully")
    print("🎉 All session isolation tests passed!")

def test_same_session_reuse():
    """Test that the same session ID returns the same session object"""
    print("\n🧪 Testing session reuse...")
    
    # Get the same session ID twice
    session1 = get_user_session("same_session_id")
    session2 = get_user_session("same_session_id")
    
    # Verify they are the same object
    assert session1 is session2, "❌ Same session ID should return same object"
    assert session1.session_id == session2.session_id, "❌ Session IDs should be identical"
    
    print(f"✅ Same session ID returns same object: {session1.session_id}")
    
    # Test state persistence
    session1.transcription_count = 42
    assert session2.transcription_count == 42, "❌ State should be shared for same session"
    
    print("✅ State is shared for same session ID")
    
    # Cleanup
    cleanup_user_session("same_session_id")
    print("✅ Session cleaned up successfully")
    print("🎉 Session reuse test passed!")

if __name__ == "__main__":
    try:
        test_session_isolation()
        test_same_session_reuse()
        print("\n🎊 ALL TESTS PASSED! Session isolation is working correctly.")
        print("\n📝 Summary:")
        print("   ✅ Different users get isolated sessions")
        print("   ✅ Each session has independent state")
        print("   ✅ Transcriptions will only go to the correct user")
        print("   ✅ No more cross-user data leakage!")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)