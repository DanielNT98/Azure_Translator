"""
ElevenLabs Text-to-Speech service with RealtimeTTS integration.

This module provides TTS using ElevenLabs API through RealtimeTTS library.
Uses native ElevenlabsEngine with continuous generator pattern for optimal
streaming performance and low latency.

Installation:
    pip install elevenlabs RealtimeTTS
"""

import logging
import queue
import threading
import time
import os
import re

from utils.timing import log_timing
from utils.device_mapper import map_sounddevice_to_pyaudio


logger = logging.getLogger("translator_bridge")


# Global variable to store forced device index for PyAudio monkey-patching
_forced_device_index = None
_pyaudio_patched = False


def _patch_pyaudio_globally(device_index):
    """
    Monkey-patch PyAudio.open() to force output device routing.
    
    MUST be called BEFORE importing RealtimeTTS to ensure PyAudio is patched
    before RealtimeTTS initializes its internal PyAudio instances.
    
    Args:
        device_index (int or None): Device index to force (e.g., Cable-A)
    """
    global _forced_device_index, _pyaudio_patched
    _forced_device_index = device_index
    
    if device_index is None or _pyaudio_patched:
        return
    
    try:
        import pyaudio
        
        # Store original open method
        original_open = pyaudio.PyAudio.open
        
        def patched_open(self, *args, **kwargs):
            """
            Patched PyAudio.open() that forces output_device_index.
            
            Args:
                *args: Variable positional arguments passed to PyAudio.open()
                **kwargs: Variable keyword arguments passed to PyAudio.open()
                
            Returns:
                PyAudio stream object
            """
            logger.info(f"[PyAudio Patch] Called with args: {args}, kwargs keys: {list(kwargs.keys())}")
            
            # Force our device for output streams
            if kwargs.get('output', False) or (not kwargs.get('input', False) and not kwargs.get('output', False)):
                old_device = kwargs.get('output_device_index', 'not set')
                kwargs['output_device_index'] = _forced_device_index
                logger.info(f"[PyAudio Patch] Forcing output_device_index: {old_device} -> {_forced_device_index}")
            else:
                logger.info(f"[PyAudio Patch] Skipping patch (input stream or no output flag)")
            
            return original_open(self, *args, **kwargs)
        
        # Apply the patch
        pyaudio.PyAudio.open = patched_open
        _pyaudio_patched = True
        logger.info(f"PyAudio globally patched to force device index: {device_index}")
        
    except ImportError:
        logger.warning("PyAudio not installed, cannot patch device routing")
    except Exception as e:
        logger.warning(f"Failed to patch PyAudio: {e}")


# Import RealtimeTTS AFTER defining the patch function
from RealtimeTTS import TextToAudioStream, ElevenlabsEngine


class ElevenLabsTTS:
    """
    ElevenLabs-based Text-to-Speech with RealtimeTTS integration.
    
    Uses RealtimeTTS's native ElevenlabsEngine with continuous generator
    pattern for optimal streaming performance and low latency. Architecture
    matches RealtimeAzureTTS for consistency.
    
    Attributes:
        engine (ElevenlabsEngine): RealtimeTTS ElevenLabs engine
        stream (TextToAudioStream): RealtimeTTS audio stream handler
        play_idx (int or None): Audio output device index
        label (str): Label for logging
        voice_id (str): ElevenLabs voice ID
        text_queue (queue.Queue): Queue for (text, timestamp) tuples
        shutdown_flag (threading.Event): Shutdown signal
        generator_active (threading.Event): Signal when generator should run
        last_translation_time (float): Timestamp of last translation
        last_tts_received_time (float): Timestamp when TTS received text
        is_playing (bool): Current playback state
    """
    
    def __init__(
        self,
        api_key: str,
        voice_id: str = "CwhRBWXzGAHq8TQ4Fs17",  # Rachel
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
            
            # Use sounddevice index directly - no mapping needed
            # PCM format bypasses PyAudio device enumeration issues
            self.pyaudio_device_idx = out_dev_idx
            logger.info(f"{label} Using output device index: {out_dev_idx}")
            
            # RealtimeTTS performance tuning parameters (same as Azure TTS)
            self.buffer_threshold = 0.0  # Minimal buffer
            self.min_sentence_length = 0  # No minimum
            self.min_first_fragment_length = 0  # Start immediately
            self.context_size = 5  # Context window
            self.force_first_fragment_after_words = 1  # Force early
            self.pre_buffer_ms = 0  # No pre-buffer for instant start
            self.lookahead_cycles = 1  # 250ms lookahead timeout
            
            # Initialize RealtimeTTS ElevenlabsEngine
            # Note: ElevenlabsEngine reads API key from ELEVENLABS_API_KEY env var
            os.environ['ELEVENLABS_API_KEY'] = api_key
            
            # Resolve voice_id to name if needed (ElevenlabsEngine may expect name)
            resolved_voice = voice_id
            try:
                if voice_id and re.match(r'^[A-Za-z0-9]{20,}$', voice_id):
                    try:
                        from elevenlabs.client import ElevenLabs
                        client = ElevenLabs(api_key=api_key or os.environ.get('ELEVENLABS_API_KEY'))
                        voices = client.voices.get_all().voices
                        match = next((v for v in voices if getattr(v, 'voice_id', '') == voice_id), None)
                        if match and getattr(match, 'name', None):
                            resolved_voice = match.name
                            logger.info(f"{label} Resolved ElevenLabs voice_id -> name: {voice_id} -> {resolved_voice}")
                    except Exception as e:
                        logger.debug(f"{label} Could not resolve voice_id to name (using id as-is): {e}")
            except Exception:
                pass
            
            logger.info(f"{label} Initializing ElevenlabsEngine with voice={resolved_voice}, model={model}, output_format=pcm_24000")
            self.engine = ElevenlabsEngine(
                voice=resolved_voice,
                model=model,
                output_format="pcm_24000"  # Request PCM to preserve device routing
            )
            logger.info(f"{label} ElevenlabsEngine created successfully (PCM mode)")
            
            # Patch engine.id to force our voice_id
            try:
                desired_voice_id = self.voice_id
                if hasattr(self.engine, 'id'):
                    original_id = self.engine.id
                    self.engine.id = desired_voice_id
                    logger.info(f"{label} Patched engine.id: {original_id} -> {desired_voice_id}")
                
                if hasattr(self.engine, 'voice_id'):
                    self.engine.voice_id = desired_voice_id
                
                # Patch synthesize method
                if hasattr(self.engine, 'synthesize'):
                    original_synthesize = self.engine.synthesize
                    
                    def patched_synthesize(text):
                        """
                        Patched synthesize method that forces voice_id.
                        
                        Ensures the correct voice_id is used for synthesis by 
                        temporarily setting and restoring the engine's voice ID.
                        
                        Args:
                            text (str): Text to synthesize
                            
                        Returns:
                            bool: True if synthesis succeeded, False otherwise
                        """
                        old_id = self.engine.id
                        try:
                            self.engine.id = desired_voice_id
                            return original_synthesize(text)
                        finally:
                            self.engine.id = desired_voice_id
                    
                    self.engine.synthesize = patched_synthesize
                    logger.info(f"{label} Patched synthesize() method to force voice_id: {desired_voice_id}")
            except Exception as e:
                logger.warning(f"{label} Could not patch voice_id: {e}")
            
            # Create audio stream with PyAudio device index
            logger.info(f"{label} Creating TextToAudioStream (PyAudio device={self.pyaudio_device_idx})")
            self.stream = TextToAudioStream(
                engine=self.engine,
                on_audio_stream_start=self._on_stream_start,
                on_audio_stream_stop=self._on_stream_stop,
                output_device_index=self.pyaudio_device_idx,
                muted=False,
                language='en',
                tokenizer='nltk',
                level=30,  # WARNING level logging
            )
            logger.info(f"{label} TextToAudioStream created successfully")
            
            # Playback queue and state
            self.text_queue = queue.Queue()
            self.shutdown_flag = threading.Event()
            self.generator_active = threading.Event()
            self.last_translation_time = None
            self.last_tts_received_time = None
            self.is_playing = False
            self.playback_lock = threading.Lock()  # Prevent concurrent playback attempts
            
            # Start background playback thread with continuous generator
            self.playback_thread = threading.Thread(
                target=self._continuous_playback_worker,
                name=f"{label}-playback",
                daemon=True
            )
            self.playback_thread.start()
            
            logger.info(f"{label} ElevenLabs TTS initialized (voice: {voice_id})")
            
        except Exception as e:
            logger.error(f"Failed to initialize ElevenLabs TTS: {e}")
            raise
    
    def _on_stream_start(self):
        """
        Callback invoked when audio streaming starts.
        
        Sets playing state and triggers optional callback.
        """
        self.is_playing = True
        logger.info(f"{self.label} Starting playback session")
        if self.on_playback_start:
            self.on_playback_start()
    
    def _on_stream_stop(self):
        """
        Callback invoked when audio streaming stops.
        
        Resets playing state.
        """
        self.is_playing = False
        logger.info(f"{self.label} Playback session ended")
    
    def _text_generator(self):
        """
        Generator that yields text chunks continuously as they arrive.
        
        This generator runs indefinitely in a loop, polling the text queue
        and yielding chunks to RealtimeTTS for synthesis. Must never exit
        the outer loop as RealtimeTTS expects continuous operation.
        
        Yields:
            str: Text chunks to be synthesized
        """
        logger.debug(f"{self.label} Text generator started - infinite loop mode")
        
        chunks_yielded = 0
        idle_cycles = 0
        max_idle_cycles = self.lookahead_cycles
        
        # OUTER INFINITE LOOP - never exits until shutdown
        while not self.shutdown_flag.is_set():
            try:
                chunk_batch = []
                
                # Collect all immediately available chunks
                while not self.text_queue.empty():
                    try:
                        text, received_time = self.text_queue.get_nowait()
                        
                        # Log synthesis start only for first chunk of batch
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
                
                # If we got chunks, yield them immediately
                if chunk_batch:
                    is_first_batch = (chunks_yielded == 0)
                    total_chars = sum(len(t) for t in chunk_batch)
                    is_short_text = (total_chars < 15)
                    has_enough_chunks = (len(chunk_batch) >= 2)
                    waited_too_long = (idle_cycles >= 2)
                    
                    # Instant yield conditions
                    should_yield_now = (is_first_batch or is_short_text or 
                                       waited_too_long or has_enough_chunks)
                    
                    if not should_yield_now and not self.text_queue.empty():
                        # Wait briefly for more chunks to batch
                        time.sleep(0.01)
                        continue
                    
                    # Pre-buffering only on very first yield
                    if chunks_yielded == 0 and self.pre_buffer_ms > 0:
                        time.sleep(self.pre_buffer_ms / 1000.0)
                    
                    # Smart concatenation and yield
                    if len(chunk_batch) > 1:
                        combined_text = " ".join(chunk_batch)
                        logger.info(f"{self.label} Yielding BATCH ({len(chunk_batch)} chunks): '{combined_text[:60]}...'")
                        logger.info(f"{self.label} About to yield to RealtimeTTS engine...")
                        yield combined_text
                        logger.info(f"{self.label} Yield completed, engine should be synthesizing...")
                        chunks_yielded += len(chunk_batch)
                    else:
                        logger.info(f"{self.label} Yielding: '{chunk_batch[0][:50]}...'")
                        logger.info(f"{self.label} About to yield to RealtimeTTS engine...")
                        yield chunk_batch[0]
                        logger.info(f"{self.label} Yield completed, engine should be synthesizing...")
                        chunks_yielded += 1
                    
                    # Reset idle counter after successful yield
                    idle_cycles = 0
                else:
                    # No chunks available - poll again
                    idle_cycles += 1
                    if idle_cycles >= max_idle_cycles:
                        logger.debug(f"{self.label} Generator idle after {chunks_yielded} total chunks")
                        idle_cycles = 0  # Reset but keep looping
                    
                    # Brief sleep before next poll
                    time.sleep(0.02)  # 20ms polling interval
                    
            except Exception as e:
                logger.error(f"{self.label} Generator error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.1)  # Brief pause on error, then continue
        
        logger.debug(f"{self.label} Text generator stopped")
    
    def _continuous_playback_worker(self):
        """
        Background worker thread maintaining continuous audio playback.
        
        Monitors the text queue and restarts playback sessions as needed
        to ensure seamless audio output. Prevents concurrent playback
        attempts through locking mechanism.
        
        This worker loops indefinitely until shutdown, mirroring the
        Azure RealtimeTTS implementation for consistency.
        """
        logger.info(f"{self.label} Playback worker started")

        try:
            while not self.shutdown_flag.is_set():
                # Wait for first/next text to arrive before starting a session
                
                if not self.generator_active.wait(timeout=1.0):
                    continue

                if self.shutdown_flag.is_set():
                    break

                # Use lock to prevent concurrent playback attempts
                with self.playback_lock:
                    logger.info(f"{self.label} Starting playback session")

                    # Feed a fresh generator for this session
                    logger.info(f"{self.label} Feeding generator to stream...")
                    self.stream.feed(self._text_generator())

                    logger.info(f"{self.label} Starting playback (device={self.play_idx})")

                    try:
                        self.stream.play(
                            # Seamless streaming settings
                            fast_sentence_fragment=True,
                            fast_sentence_fragment_allsentences=True,
                            fast_sentence_fragment_allsentences_multiple=True,

                            # Buffer & Fragment settings
                            buffer_threshold_seconds=self.buffer_threshold,
                            minimum_sentence_length=self.min_sentence_length,
                            minimum_first_fragment_length=self.min_first_fragment_length,
                            context_size=self.context_size,
                            force_first_fragment_after_words=self.force_first_fragment_after_words,

                            log_synthesized_text=True,  # Enable to see what's being synthesized
                            muted=False,
                        )
                    except Exception as play_err:
                        if not self.shutdown_flag.is_set():
                            logger.error(f"{self.label} Playback error: {play_err}")
                            import traceback
                            traceback.print_exc()

                    # When play() returns (e.g., idle), loop and wait for next text
                    logger.info(f"{self.label} Playback session ended")
                    
                    # Reset generator_active flag only if queue is empty
                    if self.text_queue.empty():
                        self.generator_active.clear()
                        logger.debug(f"{self.label} Queue empty, clearing generator_active flag")
                    
                    # Brief delay to prevent rapid looping
                    time.sleep(0.1)

        except Exception as e:
            if not self.shutdown_flag.is_set():
                logger.error(f"{self.label} Worker error: {e}")
                import traceback
                traceback.print_exc()

        logger.info(f"{self.label} Playback worker stopped")
    
    def speak(self, text: str, prev_time: float = None):
        """
        Queue text for synthesis and playback.
        
        Args:
            text (str): Text to synthesize
            prev_time (float, optional): Previous timestamp for latency calculation
        """
        if self.shutdown_flag.is_set():
            logger.warning(f"{self.label} Cannot speak, TTS is shutting down")
            return
        
        # Record timing
        self.last_tts_received_time = log_timing(
            logger, f"{self.label}-TTS", "TTS RECEIVED TEXT",
            prev_time,
            f"Text: '{text[:50]}...'" if len(text) > 50 else f"Text: '{text}'"
        )
        
        # Queue text with timestamp
        self.text_queue.put((text, self.last_tts_received_time))
        
        # Signal generator to start/continue
        self.generator_active.set()
    
    def get_voices(self):
        """
        Get list of available ElevenLabs voices.
        
        Returns:
            list: List of voice objects
        """
        try:
            return self.engine.get_voices()
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []
    
    def set_voice(self, voice_id: str):
        """
        Change the voice.
        
        Args:
            voice_id (str): ElevenLabs voice ID or name
        """
        try:
            self.engine.set_voice(voice_id)
            self.voice_id = voice_id
            logger.info(f"{self.label} Voice changed to: {voice_id}")
        except Exception as e:
            logger.error(f"Error setting voice: {e}")
    
    def stop(self):
        """Stop current playback immediately."""
        try:
            self.stream.stop()
            self.is_playing = False
        except Exception as e:
            logger.warning(f"{self.label} Error stopping playback: {e}")
    
    def shutdown(self):
        """
        Shutdown TTS service and cleanup resources.
        
        Stops playback, signals worker thread to exit, and cleans up stream.
        """
        logger.info(f"{self.label} Shutting down...")
        
        # Signal shutdown
        self.shutdown_flag.set()
        
        # Stop current playback
        try:
            self.stream.stop()
        except:
            pass
        
        # Send None to wake up worker
        try:
            self.text_queue.put(None)
        except:
            pass
        
        # Wait for worker thread
        if self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)
        
        logger.info(f"{self.label} Shutdown complete")
    
    def is_playing_audio(self):
        """
        Check if TTS is currently playing audio.
        
        Returns:
            bool: True if audio is playing
        """
        return self.is_playing or self.stream.is_playing()


# Alias for backwards compatibility
RealtimeElevenLabsTTS = ElevenLabsTTS
