"""
Azure Translator with DeepL - Main Entry Point

Bidirectional real-time translation system using DeepL Voice API for translation
and Azure Cognitive Services for Text-to-Speech synthesis.
"""

import os
import sys
import time
import traceback
import subprocess
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import configuration
from config import settings

# Import core components
from core import resolve_audio_devices, TurnManager

# Import translation components  
from translation import DeepLTranslator

# Import audio components
from audio import CapturePipe

# Import TTS (RealtimeTTS or standard Azure TTS based on config)
if settings.USE_REALTIME_TTS:
    from tts.realtime_azure_tts import RealtimeAzureTTS as TTSEngine
    print("🚀 Using RealtimeTTS for ultra-low latency")
else:
    from tts.azure_tts import FastStreamingTTS as TTSEngine
    print("📢 Using standard Azure TTS")

# Import utilities
from utils import setup_logging


def get_down_output_device(devices):
    """
    Get or auto-select output device for DOWN channel.
    
    Args:
        devices (AudioDevices): Resolved audio devices
        
    Returns:
        int or None: Output device index for DOWN channel
    """
    import logging
    import sounddevice as sd
    
    logger = logging.getLogger("translator_bridge")
    down_output_device = devices.hp_play_idx
    
    if down_output_device is None:
        logger.warning("Headphones not configured, searching for default DOWN output...")
        try:
            defaults = sd.default.device
            if isinstance(defaults, (list, tuple)) and len(defaults) == 2:
                default_out = defaults[1]
                device_info = sd.query_devices(default_out)
                device_name = device_info.get('name', 'Unknown')
                down_output_device = default_out
                logger.info(f"SUCCESS DOWN output: auto-selected '{device_name}' (idx={default_out})")
        except Exception as e:
            logger.error(f"Could not get default output device: {e}")
    
    return down_output_device


def main():
    """
    Main entry point for the translation system.
    
    Sets up logging, resolves audio devices, creates translator and capture
    components for both directions (UP and DOWN), starts services, and handles
    cleanup with automatic timing report generation.
    """
    # Setup logging
    logger, log_file_path = setup_logging(
        log_dir=os.path.join(os.path.dirname(__file__), "logs")
    )
    
    translator_up = translator_down = cap_up = cap_down = None
    
    try:
        logger.info("=" * 70)
        logger.info("DeepL Translation + Azure TTS Bridge")
        logger.info(f"Session log: {log_file_path}")
        logger.info("=" * 70)
        
        # Resolve audio devices
        devices = resolve_audio_devices(
            settings.CABLE_A_PLAYBACK,
            settings.CABLE_B_CAPTURE,
            settings.MY_HEADPHONES,
            settings.MIC_DEVICE_NAME
        )
        
        # Validate critical devices (check for None, not falsy values since idx=0 is valid)
        if devices.a_play_idx is None or devices.b_cap_idx is None or devices.mic_idx is None:
            logger.error("FAILED: Critical devices not resolved")
            return
        
        # Create turn manager for channel priority management (optional)
        from core.turn_manager import TurnManager
        turn_manager = TurnManager()
        
        # Create UP translator (your voice -> other language)
        translator_up = DeepLTranslator(
            settings.DEEPL_API_KEY,
            settings.SPEECH_KEY,
            settings.SPEECH_REGION,
            settings.SRC_LANG,
            settings.TGT_LANG_UP,
            settings.VOICE_MAP.get(settings.TGT_LANG_UP, "en-US-GuyNeural"),
            None,  # push_stream (unused)
            devices.a_play_idx,
            "A",
            "[UP]",
            capture_pipe=None
        )
        
        # Create UP capture pipe
        cap_up = CapturePipe(
            devices.mic_idx,
            None,  # push_stream (unused)
            "A",
            "[UP-VAD]",
            passthrough_device=devices.a_play_idx if settings.ENABLE_PASSTHROUGH_UP else None,
            passthrough_volume=settings.PASSTHROUGH_VOLUME_UP,
            passthrough_always=False,
            call_center_mode=settings.CALL_CENTER_MODE,
            keyboard_sound_file=settings.KEYBOARD_SOUND_FILE,
            deepl_translator=translator_up,
            turn_manager=turn_manager
        )
        
        # Link capture pipe to translator
        translator_up.capture_pipe = cap_up
        
        # Get DOWN output device
        down_output_device = get_down_output_device(devices)
        
        # Create DOWN translator (other voice -> your language)
        translator_down = DeepLTranslator(
            settings.DEEPL_API_KEY,
            settings.SPEECH_KEY,
            settings.SPEECH_REGION,
            settings.DOWN_SRC_LANG,
            settings.TGT_LANG_DOWN,
            settings.VOICE_MAP.get(settings.TGT_LANG_DOWN, "it-IT-GiuseppeNeural"),
            None,  # push_stream (unused)
            down_output_device,
            "B",
            "[DOWN]",
            capture_pipe=None
        )
        
        # Create DOWN capture pipe
        down_passthrough_device = down_output_device if settings.ENABLE_PASSTHROUGH_DOWN else None
        cap_down = CapturePipe(
            devices.b_cap_idx,
            None,  # push_stream (unused)
            "B",
            "[DOWN-VAD]",
            passthrough_device=down_passthrough_device,
            passthrough_volume=settings.PASSTHROUGH_VOLUME_DOWN,
            passthrough_always=True,  # Always pass through remote audio
            deepl_translator=translator_down,
            turn_manager=turn_manager
        )
        
        # Link capture pipe to translator
        translator_down.capture_pipe = cap_down
        
        # Link capture pipes to each other (for keyboard sound interruption)
        cap_up.other_capture_pipe = cap_down
        cap_down.other_capture_pipe = cap_up
        
        # Log configuration
        logger.info("DEBUG - Pass-through configuration:")
        logger.info(f"   UP: device={devices.a_play_idx if settings.ENABLE_PASSTHROUGH_UP else None}, "
                   f"volume={settings.PASSTHROUGH_VOLUME_UP*100:.0f}%, mode=VAD")
        logger.info(f"   DOWN: device={down_passthrough_device}, "
                   f"volume={settings.PASSTHROUGH_VOLUME_DOWN*100:.0f}%, mode=CONTINUOUS")
        
        if down_passthrough_device:
            try:
                import sounddevice as sd
                dev_info = sd.query_devices(down_passthrough_device)
                logger.info(f"   DOWN device name: {dev_info.get('name', 'Unknown')}")
            except:
                pass
        
        # Start all services
        cap_up.start()
        cap_down.start()
        translator_up.start()
        translator_down.start()
        
        # Display system status
        logger.info("=" * 70)
        logger.info("SYSTEM ACTIVE - DeepL Translation + Azure TTS:")
        logger.info(f"   Translation: DeepL Voice API (real-time WebSocket)")
        logger.info(f"   Text-to-Speech: Azure Neural TTS")
        logger.info(f"   Speech rate: {settings.SPEECH_RATE}")
        
        if settings.CALL_CENTER_MODE:
            logger.info(f"   CALL CENTER MODE: ACTIVE (keyboard sound)")
            logger.info(f"       File: {settings.KEYBOARD_SOUND_FILE}")
        
        logger.info(f"   Pass-through UP (your voice): {'ACTIVE' if settings.ENABLE_PASSTHROUGH_UP else 'DISABLED'}")
        if settings.ENABLE_PASSTHROUGH_UP:
            audio_type = "Keyboard" if settings.CALL_CENTER_MODE else "Original voice"
            logger.info(f"       {audio_type}: {settings.PASSTHROUGH_VOLUME_UP*100:.0f}% | "
                       f"Translation: {settings.TRANSLATION_VOLUME*100:.0f}%")
        
        logger.info(f"   Pass-through DOWN (remote voice): {'ACTIVE' if settings.ENABLE_PASSTHROUGH_DOWN else 'DISABLED'}")
        if settings.ENABLE_PASSTHROUGH_DOWN:
            logger.info(f"       Original: {settings.PASSTHROUGH_VOLUME_DOWN*100:.0f}% | "
                       f"Translation: {settings.TRANSLATION_VOLUME*100:.0f}%")
        
        logger.info(f"    UP: {settings.SRC_LANG} -> {settings.TGT_LANG_UP} (DeepL)")
        logger.info(f"   DOWN: {settings.DOWN_SRC_LANG} -> {settings.TGT_LANG_DOWN} (DeepL)")
        logger.info("=" * 70)
        logger.info("READY - Press Ctrl+C to exit")
        logger.info("")
        
        # Main loop
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        logger.info("")
        logger.info("STOP signal received")
    
    except Exception as e:
        logger.error(f"ERROR: {e}")
        logger.debug(traceback.format_exc())
    
    finally:
        try:
            logger.info("Shutting down...")
            
            # Stop all services
            if translator_up:
                translator_up.stop()
            if translator_down:
                translator_down.stop()
            if cap_up:
                cap_up.stop()
            if cap_down:
                cap_down.stop()
            
            logger.info("SUCCESS: Shutdown complete")
            
            # Generate timing report
            logger.info("")
            logger.info("=" * 60)
            logger.info("Generating timing report...")
            logger.info("=" * 60)
            
            try:
                # Get absolute path to utils/analyze_timing.py
                script_dir = os.path.dirname(os.path.abspath(__file__))
                analyze_script = os.path.join(script_dir, "utils", "analyze_timing.py")
                
                # Run analyze_timing.py
                result = subprocess.run(
                    [sys.executable, analyze_script, log_file_path],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    cwd=script_dir
                )
                
                # Display output
                if result.stdout:
                    print(result.stdout)
                
                # Check for report file
                report_file = log_file_path.replace('.log', '_timing_report.txt')
                if os.path.exists(report_file):
                    logger.info(f"SUCCESS: Report available: {report_file}")
                elif result.returncode == 0:
                    logger.info("SUCCESS: Report generated (see output above)")
                else:
                    logger.warning(f"WARNING: Report not generated (exit code: {result.returncode})")
                    if result.stderr:
                        error_lines = result.stderr.strip().split('\n')
                        logger.warning(f"   Error: {error_lines[0]}")
                        for line in error_lines[1:6]:  # Max 5 lines
                            logger.warning(f"   {line}")
            
            except Exception as e:
                logger.warning(f"WARNING: Could not generate report: {e}")
            
            logger.info("=" * 60)
        
        except Exception as e:
            logger.error(f"ERROR during cleanup: {e}")


if __name__ == "__main__":
    main()
