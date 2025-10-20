"""
List available ElevenLabs voices with names and IDs.

Usage (PowerShell):
    python utils/list_elevenlabs_voices.py

Reads ELEVENLABS_API_KEY from .env and prints a formatted table of voices.
Then you can copy the desired voice_id into .env as ELEVENLABS_VOICE_ID.
"""

import os
import sys
from dotenv import load_dotenv

def main():
    """
    List all available ElevenLabs voices with their IDs and categories.
    
    Fetches voices from ElevenLabs API and displays them in a formatted
    table sorted by category (premade first) and name. Prints usage
    instructions for setting voice ID in .env file.
    
    Returns:
        None
        
    Raises:
        SystemExit: If API key is missing or API request fails
    """
    load_dotenv()
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: Missing ELEVENLABS_API_KEY in .env")
        sys.exit(1)

    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        print("ERROR: Missing 'elevenlabs' package. Install dependencies and retry.")
        print("Hint: pip install elevenlabs")
        sys.exit(1)

    client = ElevenLabs(api_key=api_key)

    try:
        data = client.voices.get_all()
        voices = data.voices
    except Exception as e:
        print(f"ERROR: Failed to fetch voices: {e}")
        sys.exit(1)

    # Sort: premade first, then alphabetical
    def sort_key(v):
        """
        Generate sort key for voice object.
        
        Args:
            v: ElevenLabs voice object with category and name attributes
            
        Returns:
            tuple: Sort key (category_priority, lowercase_name)
        """
        cat = getattr(v, 'category', '') or ''
        return (0 if cat.lower() == 'premade' else 1, (getattr(v, 'name', '') or '').lower())

    voices_sorted = sorted(voices, key=sort_key)

    print("\nAvailable ElevenLabs voices:\n")
    print(f"{'#':>3}  {'NAME':<30}  {'CATEGORY':<12}  {'VOICE ID'}")
    print("-" * 80)
    for i, v in enumerate(voices_sorted, start=1):
        name = getattr(v, 'name', '') or ''
        cat = getattr(v, 'category', '') or ''
        vid = getattr(v, 'voice_id', '') or ''
        print(f"{i:>3}  {name:<30}  {cat:<12}  {vid}")

    print("\nTip:")
    print("  1) Choose a voice_id from the list above")
    print("  2) Edit .env and set ELEVENLABS_VOICE_ID=<chosen_id>")
    print("  3) (Optional) For more natural tone, try ELEVENLABS_MODEL=eleven_multilingual_v2 (a bit more latency)")

if __name__ == "__main__":
    main()
