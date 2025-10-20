
"""
ElevenLabs Text-to-Speech service with RealtimeTTS integration.

This module provides TTS using ElevenLabs API through RealtimeTTS library.
Uses streaming synthesis for low-latency audio generation.

Installation:
    pip install elevenlabs pydub
"""

import hashlib
import logging
import queue
import threading
import time
import io

from RealtimeTTS import TextToAudioStream
from elevenlabs.client import ElevenLabs
from typing import Union
import os
import traceback

# For MP3 to PCM conversion
try:
    from pydub import AudioSegment
    import pyaudio
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    logging.warning("pydub not available - MP3 decoding will be limited")

from utils.timing import log_timing


logger = logging.getLogger("translator_bridge")


class ElevenlabsVoice:
    """
    Represents an ElevenLabs voice with its metadata.
    
    Data class that encapsulates voice information including name,
    ID, category, description, and labels for voice selection.
    
    Attributes:
        name (str): Display name of the voice
        voice_id (str): Unique identifier for the voice
        category (str): Voice category (e.g., "premade", "cloned")
        description (str): Voice description
        labels (dict): Additional voice labels/metadata
    """
    
    def __init__(self, name, voice_id, category, description, labels):
        """
        Initialize ElevenLabs voice metadata.
        
        Args:
            name (str): Display name of the voice
            voice_id (str): Unique voice identifier
            category (str): Voice category
            description (str): Voice description
            labels (dict): Additional voice labels
        """
        self.name = name
        self.voice_id = voice_id
        self.category = category
        self.description = description
        self.labels = labels

    def __repr__(self):
        label_string = ", ".join(self.labels.values()) if self.labels else ""
        return f"{self.name} ({self.category}, id: {self.voice_id}, {label_string})"


class ElevenLabsEngine:
    """
    Custom RealtimeTTS-compatible engine for ElevenLabs.
    
    Adapts ElevenLabs API to work with RealtimeTTS framework.
    """
    
    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Rachel (default)
        model: str = "eleven_turbo_v2_5",  # Fast model for real-time
    ):
        """
        Initialize ElevenLabs engine.
        
        Args:
            api_key (str): ElevenLabs API key
            voice_id (str): Voice ID to use
            model (str): Model ID (eleven_turbo_v2_5 recommended for speed)
        """
        if not api_key:
            api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError(
                "Missing ElevenLabs API key. Either:\n"
                "1. Pass api_key parameter\n"
                "2. Set ELEVENLABS_API_KEY environment variable"
            )
        
        self.client = ElevenLabs(api_key=api_key)
        self.voice_id = voice_id
        self.model = model
        self.queue = queue.Queue()
        
        # Required by RealtimeTTS framework (must be set BEFORE post_init)
        self.engine_name = "elevenlabs"
        self.can_consume_generators = False  # We synthesize complete text strings
        self.timings = {}  # Placeholder for timing data
        
        logger.info(f"ElevenLabs engine initialized (voice: {voice_id}, model: {model})")
    
    def post_init(self):
        """
        Post-initialization hook called by RealtimeTTS.
        
        No additional setup needed - all attributes set in __init__.
        """
        pass
    
    def reset_audio_duration(self):
        """
        Reset audio duration tracking.
        
        Called by RealtimeTTS before each synthesis session.
        This is a no-op for ElevenLabs as we don't track duration internally.
        """
        pass
    
    def get_audio_duration(self):
        """
        Get current audio duration.
        
        Returns:
            float: Duration in seconds (always 0 for ElevenLabs)
        """
        return 0.0
    
    def shutdown(self):
        """
        Shutdown engine and cleanup resources.
        
        Called by RealtimeTTS when stream is closed.
        """
        pass
    
    def synthesize(self, text: str) -> bool:
        """
        Synthesize text to audio and put chunks in queue.
        
        Downloads complete MP3 from ElevenLabs, converts to PCM16 using pydub,
        then streams PCM chunks to queue for PyAudio playback.
        
        Args:
            text (str): Text to synthesize
            
        Returns:
            bool: True if successful
        """
        try:
            if not PYDUB_AVAILABLE:
                logger.error("pydub required for MP3 playback. Install: pip install pydub")
                return False
            
            logger.info(f"ElevenLabs synthesizing: '{text[:50]}...'")
            
            # Generate complete audio from ElevenLabs
            audio_generator = self.client.text_to_speech.convert(
                text=text,
                voice_id=self.voice_id,
                model_id=self.model,
            )
            
            # Collect all MP3 chunks
            mp3_stream = io.BytesIO()
            for chunk in audio_generator:
                if chunk:
                    mp3_stream.write(chunk)
            
            # Reset stream to beginning
            mp3_stream.seek(0)
            
            # Convert MP3 to PCM16 using pydub
            audio = AudioSegment.from_mp3(mp3_stream)
            
            # Convert to PCM16: mono, 22050 Hz, 16-bit
            audio = audio.set_channels(1).set_frame_rate(22050).set_sample_width(2)
            pcm_data = audio.raw_data
            
            # Queue PCM chunks for playback
            chunk_size = 4096
            chunk_count = 0
            for i in range(0, len(pcm_data), chunk_size):
                self.queue.put(pcm_data[i:i + chunk_size])
                chunk_count += 1
            
            logger.info(f"ElevenLabs converted {len(pcm_data)} bytes PCM, {chunk_count} chunks")
            return True
            
        except Exception as e:
            logger.error(f"ElevenLabs synthesis error: {e}")
            traceback.print_exc()
            return False
    
    def get_stream_info(self):
        """Return audio stream format info for RealtimeTTS."""
        # Return PCM16 format info (16-bit signed int, mono, 22050 Hz)
        return pyaudio.paInt16, 1, 22050
    
    def get_voices(self):
        """Get available voices from ElevenLabs."""
        try:
            fetched_voices = self.client.voices.get_all()
            voices_list = fetched_voices.voices
            
            voice_objects = []
            for voice in voices_list:
                voice_object = ElevenlabsVoice(
                    name=voice.name,
                    voice_id=voice.voice_id,
                    category=voice.category,
                    description=voice.description,
                    labels=voice.labels,
                )
                voice_objects.append(voice_object)
            
            return voice_objects
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []
    
    def set_voice(self, voice: Union[str, ElevenlabsVoice]):
        """Set voice by name or voice object."""
        if isinstance(voice, ElevenlabsVoice):
            logger.info(f"Setting voice to {voice.name}")
            self.voice_id = voice.voice_id
            return
        
        # Search by name
        installed_voices = self.get_voices()
        for installed_voice in installed_voices:
            if voice in installed_voice.name:
                logger.info(f"Setting voice to {installed_voice.name}")
                self.voice_id = installed_voice.voice_id
                return
        
        logger.warning(f"Voice {voice} not found.")


class ElevenLabsTTS:
    """
    ElevenLabs-based Text-to-Speech with RealtimeTTS integration.
    
    Provides streaming TTS synthesis using ElevenLabs API through
    RealtimeTTS framework for low-latency audio playback.
    
    Attributes:
        engine (ElevenLabsEngine): Custom ElevenLabs engine
        stream (TextToAudioStream): RealtimeTTS audio stream handler
        play_idx (int or None): Audio output device index
        label (str): Label for logging
        voice_id (str): ElevenLabs voice ID
        text_queue (queue.Queue): Queue for text chunks
        shutdown_flag (threading.Event): Shutdown signal
    """
    
    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Rachel
        out_dev_idx: int = None,
        label: str = "ElevenLabs-TTS",
        model: str = "eleven_turbo_v2_5",
        on_playback_start=None,
    ):
        """
        Initialize ElevenLabs TTS service.
        
        Args:
            api_key (str): ElevenLabs API key
            voice_id (str): ElevenLabs voice ID
            out_dev_idx (int or None): Audio output device index
            label (str): Label for logging
            model (str): ElevenLabs model ID
            on_playback_start (callable): Callback when playback starts
        """
        try:
            self.play_idx = out_dev_idx
            self.label = label
            self.voice_id = voice_id
            self.on_playback_start = on_playback_start
            
            # RealtimeTTS performance tuning
            self.buffer_threshold = 0.1  # 100ms buffer
            self.min_sentence_length = 0
            self.min_first_fragment_length = 0
            self.context_size = 20
            self.force_first_fragment_after_words = 8
            self.pre_buffer_ms = 20
            self.lookahead_cycles = 5
            
            # Create ElevenLabs engine
            self.engine = ElevenLabsEngine(
                api_key=api_key,
                voice_id=voice_id,
                model=model,
            )
            
            # Create RealtimeTTS stream
            self.stream = TextToAudioStream(
                self.engine,
                on_audio_stream_start=self._on_stream_start,
                on_audio_stream_stop=self._on_stream_stop,
                output_device_index=out_dev_idx,
                muted=False,
                language='en',
                tokenizer='nltk',
                level=30,  # WARNING level
            )
            
            # Playback queue and state
            self.text_queue = queue.Queue()
            self.shutdown_flag = threading.Event()
            self.generator_active = threading.Event()
            self.last_text_hash = ""
            self.last_translation_time = None
            self.last_tts_received_time = None
            self.is_playing = False
            
            # Start background playback thread
            self.playback_thread = threading.Thread(
                target=self._continuous_playback_worker,
                daemon=True,
                name=f"{label}_playback"
            )
            self.playback_thread.start()
            
            logger.info(f"{self.label} ElevenLabs TTS initialized (voice: {voice_id})")
            
        except Exception as e:
            logger.error(f"{self.label} ElevenLabs TTS init failed: {e}")
            raise
    
    def _on_stream_start(self):
        """Callback when audio streaming starts."""
        self.is_playing = True
        if self.on_playback_start:
            self.on_playback_start()
        logger.debug(f"{self.label} Stream started")
    
    def _on_stream_stop(self):
        """Callback when audio streaming stops."""
        self.is_playing = False
        logger.debug(f"{self.label} Stream stopped")
    
    def speak(self, text):
        """
        Synthesize and queue text for playback.
        
        Args:
            text (str): Text to synthesize
            
        Returns:
            bool: True if queued successfully
        """
        if not text or not text.strip():
            return False
        
        # Deduplication
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash == self.last_text_hash:
            logger.debug(f"{self.label} Skipping duplicate text")
            return False
        
        self.last_text_hash = text_hash
        
        # Log timing
        self.last_tts_received_time = log_timing(
            logger, f"{self.label}-TTS", "TTS RECEIVED TEXT",
            self.last_translation_time,
            f"Text: '{text[:50]}...'" if len(text) > 50 else f"Text: '{text}'"
        )
        
        # Queue for playback
        self.text_queue.put((text, self.last_tts_received_time))
        self.generator_active.set()
        
        return True
    
    def _text_generator(self):
        """Generator that yields text chunks continuously."""
        logger.debug(f"{self.label} Text generator started")
        
        while not self.shutdown_flag.is_set():
            try:
                if not self.generator_active.wait(timeout=0.1):
                    continue
                
                # Pre-buffering
                if self.pre_buffer_ms > 0:
                    time.sleep(self.pre_buffer_ms / 1000.0)
                
                chunks_yielded = 0
                idle_cycles = 0
                max_idle_cycles = self.lookahead_cycles
                
                while not self.shutdown_flag.is_set():
                    chunk_batch = []
                    
                    # Collect available chunks
                    while not self.text_queue.empty():
                        try:
                            text, received_time = self.text_queue.get_nowait()
                            
                            if not chunk_batch:
                                log_timing(
                                    logger, f"{self.label}-TTS", "TTS SYNTHESIS START",
                                    received_time,
                                    f"Text: '{text[:30]}...'" if len(text) > 30 else f"Text: '{text}'"
                                )
                            
                            chunk_batch.append(text)
                            idle_cycles = 0
                            
                        except queue.Empty:
                            break
                    
                    if chunk_batch:
                        # Yield chunks
                        if len(chunk_batch) > 1:
                            combined_text = " ".join(chunk_batch)
                            logger.info(f"{self.label} Yielding BATCH ({len(chunk_batch)} chunks): '{combined_text[:60]}...'")
                            yield combined_text
                            chunks_yielded += len(chunk_batch)
                        else:
                            logger.info(f"{self.label} Yielding: '{chunk_batch[0][:50]}...'")
                            yield chunk_batch[0]
                            chunks_yielded += 1
                    else:
                        idle_cycles += 1
                        if idle_cycles >= max_idle_cycles:
                            logger.debug(f"{self.label} Generator idle, ending cycle (yielded {chunks_yielded} chunks)")
                            break
                        time.sleep(0.01)
                
                if self.text_queue.empty():
                    self.generator_active.clear()
                    
            except Exception as e:
                logger.error(f"{self.label} Generator error: {e}")
                break
        
        logger.debug(f"{self.label} Text generator stopped")
    
    def _continuous_playback_worker(self):
        """Background worker for continuous playback."""
        logger.info(f"{self.label} Playback worker started")
        
        while not self.shutdown_flag.is_set():
            try:
                if not self.generator_active.wait(timeout=1.0):
                    continue
                
                logger.info(f"{self.label} [FAST] Starting playback session")
                session_start = time.time()
                
                try:
                    self.stream.feed(self._text_generator())
                    self.stream.play(
                        fast_sentence_fragment=True,
                        fast_sentence_fragment_allsentences=True,
                        fast_sentence_fragment_allsentences_multiple=True,
                        buffer_threshold_seconds=self.buffer_threshold,
                        minimum_sentence_length=self.min_sentence_length,
                        minimum_first_fragment_length=self.min_first_fragment_length,
                        context_size=self.context_size,
                        force_first_fragment_after_words=self.force_first_fragment_after_words,
                        log_synthesized_text=False,
                        muted=False,
                    )
                    
                    session_duration = (time.time() - session_start) * 1000
                    logger.info(f"{self.label} [DONE] Playback session complete: {session_duration:.1f}ms")
                    
                except Exception as e:
                    logger.error(f"{self.label} Playback error: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    
            except Exception as e:
                logger.error(f"{self.label} Playback worker error: {e}")
        
        logger.info(f"{self.label} Playback worker stopped")
    
    def stop_playback(self):
        """Stop current playback."""
        try:
            self.stream.stop()
            logger.info(f"{self.label} Playback stopped")
        except Exception as e:
            logger.error(f"{self.label} Stop error: {e}")
    
    def clear_queue(self):
        """Clear pending synthesis tasks."""
        cleared = 0
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        
        if cleared > 0:
            logger.info(f"{self.label} Cleared {cleared} pending items")
    
    def shutdown(self):
        """Shutdown TTS service."""
        logger.info(f"{self.label} Shutting down...")
        
        self.shutdown_flag.set()
        self.generator_active.set()
        
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)
        
        try:
            self.stream.stop()
        except:
            pass
        
        logger.info(f"{self.label} Shutdown complete")

"""
Instructions: ensure "pip install keyboard" before running this script.
"""
if __name__ == "__main__":
    from RealtimeTTS import TextToAudioStream, ElevenlabsEngine
    import time
    import signal
    import keyboard

    def signal_handler(sig, frame):
        """
        Handle Ctrl+C interrupt signal.
        
        Args:
            sig: Signal number
            frame: Current stack frame
        """
        print("\nCtrl+C detected. Cleaning up...")
        stream.stop()
        import sys
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    def dummy_generator():
        """
        Generate test sentences for TTS demonstration.
        
        Yields:
            str: Test sentences explaining pause functionality
        """
        yield "Press spacebar to pause or resume playback. Press Q to quit. "
        yield "Quick technical note: When you press pause, there may be a brief delay before the audio stops with ElevenlabsEngine. "
        yield "This happens because the audio player (MPV) pre-buffers some audio data for smooth playback - like water still flowing from a pipe even after you close the tap. "

    engine = ElevenlabsEngine()
    voices = engine.get_voices()

    for voice in voices:
        print(f"Voice: {voice.name}, ID: {voice.voice_id}, Category: {voice.category}, Description: {voice.description}")

    stream = TextToAudioStream(engine)

    print("Starting stream - Press SPACE to pause/resume, Q to quit")

    stream.feed(dummy_generator()).play_async(log_synthesized_text=True)

    is_playing = True
    while True:
        if not stream.is_playing():
            print("\nStream finished.")
            break

        if keyboard.is_pressed('space'):
            if is_playing:
                stream.pause()
                print("Paused")
            else:
                stream.resume()
                print("Resumed")
            is_playing = not is_playing
            time.sleep(0.1)  # Debounce
            
        if keyboard.is_pressed('q'):
            print("\nQuitting...")
            break
            
        time.sleep(0.1)
    
    stream.stop()