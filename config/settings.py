"""
Configuration settings for Azure Translator with DeepL.

This module contains all configuration parameters, constants, and environment
variable loading for the translation system.
"""

import os
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()


def get_env(name, default=None):
    """
    Retrieve environment variable with fallback to default value.
    
    Args:
        name (str): Environment variable name
        default (Any, optional): Default value if variable not set. Defaults to None.
        
    Returns:
        str or None: Environment variable value or default
    """
    value = os.getenv(name)
    return value if (value is not None and value.strip() != "") else default


# Azure Speech Service Configuration
SPEECH_KEY = get_env("SPEECH__SERVICE__KEY", "<YOUR_SPEECH_KEY>")
SPEECH_REGION = get_env("SPEECH__SERVICE__REGION", "westeurope")

# DeepL API Configuration
DEEPL_API_KEY = get_env("DEEPL_API_KEY", "<YOUR_DEEPL_API_KEY>")
DEEPL_API_URL = get_env("DEEPL_API_URL", "https://api.deepl.com/v1/voice/realtime")

# Language Configuration
SRC_LANG = get_env("SRC_LANG", "it")  # DeepL uses "it" not "it-IT"
TGT_LANG_UP = get_env("TGT_LANG_UP", "en")
DOWN_SRC_LANG = get_env("DOWN_SRC_LANG", "en")
TGT_LANG_DOWN = get_env("TGT_LANG_DOWN", "it")

# Audio Device Configuration
CABLE_A_PLAYBACK = get_env("CABLE_A_PLAYBACK", "CABLE-A Input")
CABLE_B_CAPTURE = get_env("CABLE_B_CAPTURE", "CABLE-B Output")
MY_HEADPHONES = get_env("MY_HEADPHONES", "")
MIC_DEVICE_NAME = get_env("MIC_DEVICE_NAME", "Jack Mic (Realtek(R) Audio)")

# Audio Parameters
SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 10
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

# Voice Activity Detection (VAD) Parameters
ENERGY_THRESHOLD = float(get_env("VAD_THRESHOLD", "0.025"))
HANGOVER_MS = int(get_env("VAD_HANGOVER_MS", "200"))
MIN_SPEECH_MS = int(get_env("MIN_SPEECH_MS", "150"))
TURN_HYST_MS = int(get_env("TURN_HYST_MS", "250"))
SOFT_LOCK_MS = int(get_env("SOFT_LOCK_MS", "500"))
BARGE_THRESHOLD = float(get_env("BARGE_THRESHOLD", "0.050"))

# Text Chunking Parameters
FIRST_CHUNK_WORDS = int(get_env("FIRST_CHUNK_WORDS", "2"))
SUBSEQUENT_CHUNK_WORDS = int(get_env("SUBSEQUENT_CHUNK_WORDS", "5"))
FIRST_CHUNK_STABILITY_MS = int(get_env("FIRST_CHUNK_STABILITY_MS", "250"))
SUBSEQUENT_STABILITY_MS = int(get_env("SUBSEQUENT_STABILITY_MS", "500"))

# Text-to-Speech Parameters
SPEECH_RATE = get_env("SPEECH_RATE", "+35%")

# Audio Pass-through Configuration
ENABLE_PASSTHROUGH_UP = get_env("ENABLE_PASSTHROUGH_UP", "1") == "1"
PASSTHROUGH_VOLUME_UP = float(get_env("PASSTHROUGH_VOLUME_UP", "0.05"))
ENABLE_PASSTHROUGH_DOWN = get_env("ENABLE_PASSTHROUGH_DOWN", "1") == "1"
PASSTHROUGH_VOLUME_DOWN = float(get_env("PASSTHROUGH_VOLUME_DOWN", "0.05"))
TRANSLATION_VOLUME = float(get_env("TRANSLATION_VOLUME", "1.0"))

# Call Center Mode Configuration
CALL_CENTER_MODE = get_env("CALL_CENTER_MODE", "0") == "1"
KEYBOARD_SOUND_FILE = get_env("KEYBOARD_SOUND_FILE", "computer-keyboard-typing-290582.wav")

# Advanced Chunking Parameters
MAX_CHUNK_DELAY_MS = int(get_env("MAX_CHUNK_DELAY_MS", "3000"))
MAX_PARTIAL_DURATION_MS = int(get_env("MAX_PARTIAL_DURATION_MS", "4000"))
GROWTH_THRESHOLD_WORDS = int(get_env("GROWTH_THRESHOLD_WORDS", "15"))
MIN_DELAY_BETWEEN_CHUNKS_MS = int(get_env("MIN_DELAY_BETWEEN_CHUNKS_MS", "800"))

# Recognition Mode
USE_PARTIALS = get_env("USE_PARTIALS", "0")
FINAL_SEG_MS = get_env("FINAL_SEG_MS", "100")

# Voice Name Mapping
VOICE_MAP = {
    "it": "it-IT-GiuseppeNeural",
    "es": "es-ES-ElviraNeural",
    "en": "en-US-GuyNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
}
