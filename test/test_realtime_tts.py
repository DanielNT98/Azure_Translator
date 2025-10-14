"""
Test script for RealtimeTTS Azure integration.

Quick test to verify RealtimeAzureTTS module works correctly.
"""

import os
import sys
import time
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts.realtime_azure_tts import RealtimeAzureTTS

# Load environment variables
load_dotenv()

def test_realtime_tts():
    """Test RealtimeTTS with a simple phrase."""
    
    print("🧪 Testing RealtimeAzureTTS...")
    print("-" * 50)
    
    # Get Azure credentials
    azure_key = os.getenv("SPEECH__SERVICE__KEY")
    azure_region = os.getenv("AZURE_SPEECH_REGION", "westeurope")
    
    if not azure_key:
        print("❌ ERROR: AZURE_SPEECH_KEY not found in .env")
        return False
    
    try:
        # Initialize TTS
        print(f"🔧 Initializing RealtimeTTS...")
        print(f"   Region: {azure_region}")
        print(f"   Voice: en-US-AndrewMultilingualNeural")
        
        tts = RealtimeAzureTTS(
            speech_key=azure_key,
            region=azure_region,
            voice_name="en-US-AndrewMultiingualNeural",
            out_dev_idx=None,  # Default output device
            label="TEST",
            speech_rate="+40%",
        )
        
        print("✅ RealtimeTTS initialized successfully!")
        print()
        
        # Test phrases
        test_phrases = [
            "Hello, this is a test of the RealtimeTTS system.",
            "The quick brown fox jumps over the lazy dog.",
            "Real-time text to speech is amazing!",
        ]
        
        for i, phrase in enumerate(test_phrases, 1):
            print(f"🎤 Test {i}/{len(test_phrases)}: '{phrase}'")
            
            start_time = time.time()
            tts.speak(phrase)
            
            # Wait for synthesis to start
            time.sleep(0.5)
            
            # Wait for playback to finish
            while tts.is_playing:
                time.sleep(0.1)
            
            elapsed = time.time() - start_time
            print(f"   ✅ Completed in {elapsed:.2f}s")
            print()
            
            # Pause between phrases
            time.sleep(1.0)
        
        # Cleanup
        print("🧹 Shutting down...")
        tts.shutdown()
        
        print("✅ All tests passed!")
        print("-" * 50)
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_realtime_tts()
    sys.exit(0 if success else 1)
