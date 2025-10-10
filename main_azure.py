"""
Azure Translation System - Main Entry Point
Uses Azure Speech Translation API with balanced chunking for real-time translation.
"""

import os
import sys
import time
import logging
import traceback
import azure.cognitiveservices.speech as speechsdk

# Add project root to path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Import configuration
from config.settings import (
    SPEECH_KEY, SPEECH_REGION,
    SRC_LANG, TGT_LANG_UP, DOWN_SRC_LANG, TGT_LANG_DOWN,
    SAMPLE_RATE, CHANNELS, FRAME_SAMPLES,
    CABLE_A_PLAYBACK, CABLE_B_CAPTURE, MY_HEADPHONES, MIC_DEVICE_NAME,
    ENABLE_PASSTHROUGH_UP, PASSTHROUGH_VOLUME_UP,
    ENABLE_PASSTHROUGH_DOWN, PASSTHROUGH_VOLUME_DOWN,
    TRANSLATION_VOLUME, CALL_CENTER_MODE, KEYBOARD_SOUND_FILE,
    SPEECH_RATE, USE_PARTIALS, FINAL_SEG_MS,
    MAX_CHUNK_DELAY_MS, GROWTH_THRESHOLD_WORDS, MAX_PARTIAL_DURATION_MS,
    FIRST_CHUNK_WORDS, FIRST_CHUNK_STABILITY_MS, SUBSEQUENT_CHUNK_WORDS,
    VOICE_MAP
)

# Import core modules
from core.audio_devices import resolve_audio_devices
from core.turn_manager import TurnManager

# Import translation modules
from translation.azure_translator import AzureTranslator

# Import TTS module
from tts.azure_tts import FastStreamingTTS

# Import audio module
from audio.capture_pipe import CapturePipe


# Setup logging
log_dir = os.path.join(script_dir, "logs")
os.makedirs(log_dir, exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S")
log_filename = os.path.join(log_dir, f"azure_translator_session_{timestamp}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def main():
    """Main entry point for Azure translation system."""
    translator_up = translator_down = None
    tts_up = tts_down = None
    cap_up = cap_down = None
    push_up = push_down = None
    turn_manager = None
    
    try:
        log.info("=" * 70)
        log.info("Azure Translation System - Refactored Architecture")
        log.info("=" * 70)
        
        # Resolve audio devices
        log.info("Resolving audio devices...")
        devices = resolve_audio_devices(
            cable_a_playback=CABLE_A_PLAYBACK,
            cable_b_capture=CABLE_B_CAPTURE,
            headphones=MY_HEADPHONES,
            mic_name=MIC_DEVICE_NAME
        )
        
        if devices.mic_idx is None:
            log.error("Failed to resolve microphone device")
            return
        
        if devices.a_play_idx is None:
            log.error("Failed to resolve CABLE-A output device")
            return
        
        if devices.b_cap_idx is None:
            log.error("Failed to resolve CABLE-B input device")
            return
        
        log.info(f"Devices resolved: mic={devices.mic_idx}, "
                f"cable_a_out={devices.a_play_idx}, cable_b_in={devices.b_cap_idx}, "
                f"headphones={devices.hp_play_idx}")
        
        # Create turn manager
        turn_manager = TurnManager()
        log.info("Turn manager created")
        
        # Create Azure push streams
        log.info("Creating Azure push audio streams...")
        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=SAMPLE_RATE,
            bits_per_sample=16,
            channels=CHANNELS
        )
        push_up = speechsdk.audio.PushAudioInputStream(audio_format)
        push_down = speechsdk.audio.PushAudioInputStream(audio_format)
        
        # Get voice names
        voice_up = VOICE_MAP.get(TGT_LANG_UP, "en-US-GuyNeural")
        voice_down = VOICE_MAP.get(TGT_LANG_DOWN, "it-IT-GiuseppeNeural")
        
        # Convert language codes for Azure (Azure needs full locale)
        # Map common short codes to proper Azure locale codes
        lang_locale_map = {
            "it": "it-IT",
            "en": "en-US",
            "es": "es-ES",
            "de": "de-DE",
            "fr": "fr-FR"
        }
        
        src_lang_azure = lang_locale_map.get(SRC_LANG, SRC_LANG) if '-' not in SRC_LANG else SRC_LANG
        down_src_lang_azure = lang_locale_map.get(DOWN_SRC_LANG, DOWN_SRC_LANG) if '-' not in DOWN_SRC_LANG else DOWN_SRC_LANG
        
        log.info(f"Languages: UP ({src_lang_azure} → {TGT_LANG_UP}), "
                f"DOWN ({down_src_lang_azure} → {TGT_LANG_DOWN})")
        
        # Create TTS engines
        log.info("Creating TTS engines...")
        tts_up = FastStreamingTTS(
            speech_key=SPEECH_KEY,
            region=SPEECH_REGION,
            voice_name=voice_up,
            out_dev_idx=devices.a_play_idx,
            label="[UP-TTS]",
            speech_rate=SPEECH_RATE
        )
        
        # Determine DOWN output device (headphones or default)
        down_output_device = devices.hp_play_idx
        if down_output_device is None:
            log.warning("Headphones not configured, using default output for DOWN")
            import sounddevice as sd
            try:
                default_out = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else None
                if default_out is not None:
                    down_output_device = default_out
                    log.info(f"Using default output device idx={default_out} for DOWN")
            except Exception as e:
                log.error(f"Failed to get default output device: {e}")
        
        tts_down = FastStreamingTTS(
            speech_key=SPEECH_KEY,
            region=SPEECH_REGION,
            voice_name=voice_down,
            out_dev_idx=down_output_device,
            label="[DOWN-TTS]",
            speech_rate=SPEECH_RATE
        )
        
        # Create capture pipes FIRST (before translators)
        log.info("Creating capture pipes...")
        
        cap_up = CapturePipe(
            input_device_idx=devices.mic_idx,
            push_stream=push_up,
            channel='A',
            label='[UP-CAPTURE]',
            vad=None,  # Will be created internally
            turn_manager=turn_manager,
            passthrough_device=devices.a_play_idx if ENABLE_PASSTHROUGH_UP else None,
            passthrough_volume=PASSTHROUGH_VOLUME_UP,
            passthrough_always=False,  # Only when speaking
            call_center_mode=CALL_CENTER_MODE,
            keyboard_sound_file=KEYBOARD_SOUND_FILE
        )
        
        down_passthrough_device = down_output_device if ENABLE_PASSTHROUGH_DOWN else None
        cap_down = CapturePipe(
            input_device_idx=devices.b_cap_idx,
            push_stream=push_down,
            channel='B',
            label='[DOWN-CAPTURE]',
            vad=None,  # Will be created internally
            turn_manager=turn_manager,
            passthrough_device=down_passthrough_device,
            passthrough_volume=PASSTHROUGH_VOLUME_DOWN,
            passthrough_always=True  # Always, to hear all remote audio
        )
        
        # Link capture pipes for keyboard control
        cap_up.other_capture_pipe = cap_down
        cap_down.other_capture_pipe = cap_up
        
        # Create Azure translators
        log.info("Creating Azure translators...")
        
        translator_up = AzureTranslator(
            from_lang=src_lang_azure,
            to_lang=TGT_LANG_UP,
            voice_name=voice_up,
            push_stream=push_up,
            tts=tts_up,
            channel='A',
            label='[UP]',
            turn_manager=turn_manager,
            capture_pipe=cap_up
        )
        
        translator_down = AzureTranslator(
            from_lang=down_src_lang_azure,
            to_lang=TGT_LANG_DOWN,
            voice_name=voice_down,
            push_stream=push_down,
            tts=tts_down,
            channel='B',
            label='[DOWN]',
            turn_manager=turn_manager,
            capture_pipe=cap_down
        )
        
        # Cross-link translators with capture pipes for keyboard control
        cap_up.azure_translator = translator_up
        cap_down.azure_translator = translator_down
        
        # Start all services
        log.info("Starting capture pipes...")
        cap_up.start()
        cap_down.start()
        
        log.info("Starting translators...")
        translator_up.start()
        translator_down.start()
        
        # Print configuration summary
        log.info("=" * 70)
        log.info("SYSTEM ACTIVE - Azure Translation Mode")
        log.info(f"   Speech synthesis speed: {SPEECH_RATE}")
        log.info(f"   Partial results: {'ENABLED' if USE_PARTIALS == '1' else 'DISABLED'}")
        log.info(f"   Final segmentation: {FINAL_SEG_MS}ms")
        
        if CALL_CENTER_MODE:
            log.info(f"   CALL CENTER MODE: ACTIVE (keyboard sound)")
            log.info(f"       └─ File: {KEYBOARD_SOUND_FILE}")
        
        log.info(f"   Pass-through UP (your voice): {'ACTIVE' if ENABLE_PASSTHROUGH_UP else 'INACTIVE'}")
        if ENABLE_PASSTHROUGH_UP:
            audio_type = "Keyboard" if CALL_CENTER_MODE else "Original voice"
            log.info(f"       └─ {audio_type}: {PASSTHROUGH_VOLUME_UP*100:.0f}% | Translation: {TRANSLATION_VOLUME*100:.0f}%")
        
        log.info(f"   Pass-through DOWN (remote voice): {'ACTIVE' if ENABLE_PASSTHROUGH_DOWN else 'INACTIVE'}")
        if ENABLE_PASSTHROUGH_DOWN:
            log.info(f"       └─ Original volume: {PASSTHROUGH_VOLUME_DOWN*100:.0f}% | Translation: {TRANSLATION_VOLUME*100:.0f}%")
        
        log.info(f"   Chunk timeout: {MAX_CHUNK_DELAY_MS}ms")
        log.info(f"   Growth threshold: {GROWTH_THRESHOLD_WORDS} words")
        log.info(f"   Max partial duration: {MAX_PARTIAL_DURATION_MS}ms")
        log.info(f"   First chunk: {FIRST_CHUNK_WORDS}w @ {FIRST_CHUNK_STABILITY_MS}ms")
        log.info(f"   Subsequent: {SUBSEQUENT_CHUNK_WORDS}w")
        log.info(f"   UP: {src_lang_azure} → {TGT_LANG_UP}")
        log.info(f"   DOWN: {down_src_lang_azure} → {TGT_LANG_DOWN}")
        log.info("=" * 70)
        log.info("Ready - Press Ctrl+C to exit\n")
        
        # Main loop
        while True:
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        log.info("\nShutdown requested by user")
    except Exception as e:
        log.error(f"Fatal error: {e}")
        log.error(traceback.format_exc())
    finally:
        # Cleanup
        log.info("Shutting down...")
        
        try:
            if translator_up:
                translator_up.stop()
        except Exception as e:
            log.error(f"Error stopping UP translator: {e}")
        
        try:
            if translator_down:
                translator_down.stop()
        except Exception as e:
            log.error(f"Error stopping DOWN translator: {e}")
        
        try:
            if tts_up:
                tts_up.shutdown()
        except Exception as e:
            log.error(f"Error stopping UP TTS: {e}")
        
        try:
            if tts_down:
                tts_down.shutdown()
        except Exception as e:
            log.error(f"Error stopping DOWN TTS: {e}")
        
        try:
            if cap_up:
                cap_up.stop()
        except Exception as e:
            log.error(f"Error stopping UP capture: {e}")
        
        try:
            if cap_down:
                cap_down.stop()
        except Exception as e:
            log.error(f"Error stopping DOWN capture: {e}")
        
        try:
            if push_up:
                push_up.close()
        except Exception as e:
            log.error(f"Error closing UP push stream: {e}")
        
        try:
            if push_down:
                push_down.close()
        except Exception as e:
            log.error(f"Error closing DOWN push stream: {e}")
        
        log.info("=" * 70)
        log.info("Azure Translation System terminated")
        log.info(f"Log file: {log_filename}")
        log.info("=" * 70)
        
        # Generate timing report
        try:
            import subprocess
            analyzer_path = os.path.join(script_dir, "utils", "analyze_timing.py")
            if os.path.exists(analyzer_path):
                log.info("Generating timing analysis report...")
                subprocess.run([sys.executable, analyzer_path, log_filename])
        except Exception as e:
            log.error(f"Failed to generate timing report: {e}")


if __name__ == "__main__":
    main()
