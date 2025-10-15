"""
RealtimeTTS-based Azure Text-to-Speech service with ultra-low latency.

This module provides ultra-low latency TTS using the RealtimeTTS library
with Azure Cognitive Services backend. Features streaming synthesis,
sentence-level chunking, and queue-based audio playback.

Installation:
    pip install RealtimeTTS

Documentation:
    https://github.com/KoljaB/RealtimeTTS
"""

import hashlib
import logging
import queue
import threading
import time

from RealtimeTTS import TextToAudioStream, AzureEngine

from utils.timing import log_timing


logger = logging.getLogger("translator_bridge")


class RealtimeAzureTTS:
    """
    Ultra-low latency Text-to-Speech using RealtimeTTS with Azure backend.
    
    Provides streaming TTS synthesis with:
    - Sub-second latency for first audio chunk
    - Sentence-level streaming (starts speaking before full text is synthesized)
    - Azure Neural Voices support
    - Configurable speech rate and voice
    - Thread-safe queue-based playback
    
    Attributes:
        engine (AzureEngine): RealtimeTTS Azure engine
        stream (TextToAudioStream): Audio streaming handler
        play_idx (int or None): Audio output device index
        label (str): Label for logging identification
        voice_name (str): Azure neural voice name
        speech_rate (str): SSML speech rate (e.g., "+40%")
        play_queue (queue.Queue): Queue for audio playback
        shutdown_flag (threading.Event): Shutdown signal
        on_playback_start (callable): Callback when playback starts
        playback_thread (threading.Thread): Background playback worker
        last_text_hash (str): Hash of last text (deduplication)
        last_translation_time (float): Timestamp of last translation
        last_tts_received_time (float): Timestamp when TTS received text
        is_playing (bool): Current playback state
    """
    
    def __init__(self, speech_key, region, voice_name, out_dev_idx, label, 
                 speech_rate="+40%", on_playback_start=None):
        """
        Initialize RealtimeTTS Azure engine.
        
        Args:
            speech_key (str): Azure Speech Service subscription key
            region (str): Azure region (e.g., "westeurope")
            voice_name (str): Azure neural voice name (e.g., "en-US-AndrewMultilingualNeural")
            out_dev_idx (int or None): Audio output device index (None = default)
            label (str): Label for logging
            speech_rate (str, optional): SSML speech rate. Defaults to "+40%".
            on_playback_start (callable, optional): Callback when playback starts
            
        Raises:
            Exception: If RealtimeTTS initialization fails
        """
        try:
            self.play_idx = out_dev_idx
            self.label = label
            self.voice_name = voice_name
            self.speech_rate = speech_rate
            self.on_playback_start = on_playback_start
            
            # RealtimeTTS performance tuning parameters
            #LOW LATENCY + HIGH FLUIDITY MODE: Optimized for fast start, smooth continuation
            self.buffer_threshold = 0.0  # ZERO buffer for instant start (first chunk)
            self.buffer_threshold_later = 0.2  # 200ms buffer for subsequent chunks (smoother)
            self.min_sentence_length = 0  # No minimum - accept any fragment size
            self.min_first_fragment_length = 0  # Start as fast as possible
            self.context_size = 30  # Extended context for better phrasing
            self.force_first_fragment_after_words = 15 # Force earlier fragments
            
            # Pre-buffering and lookahead settings
            self.pre_buffer_ms = 0  # NO pre-buffer for first chunk (instant start!)
            self.pre_buffer_ms_later = 80  # 80ms pre-buffer for subsequent chunks (batching)
            self.lookahead_cycles = 8  # Wait up to 400ms for more chunks when idle
            
            # State tracking for adaptive behavior
            self.is_first_chunk = True  # Track if this is the first chunk of a session
            
            # Convert speech_rate from SSML format (+40%) to float (0.4)
            rate_value = 0.0
            if speech_rate:
                # Extract number from "+XX%" or "-XX%"
                import re
                match = re.search(r'([+-]?\d+)%', speech_rate)
                if match:
                    rate_value = float(match.group(1)) / 100.0
            
            # Initialize Azure engine with RealtimeTTS
            # Rate/Pitch are multipliers: 0.0 = normal, 0.5 = 50% faster/higher, -0.5 = 50% slower/lower
            # Audio format: 16kHz provides good balance of quality and speed for real-time TTS
            self.engine = AzureEngine(
                speech_key=speech_key,
                service_region=region,
                voice=voice_name,
                rate=rate_value,  # Convert +40% to 0.4
                pitch=0.0,  # Normal pitch (no modulation)
                audio_format="riff-16khz-16bit-mono-pcm",  # 16kHz for low latency
            )
            
            # Create streaming handler with optimized settings for low latency
            self.stream = TextToAudioStream(
                engine=self.engine,
                on_audio_stream_start=self._on_stream_start,
                on_audio_stream_stop=self._on_stream_stop,
                output_device_index=out_dev_idx,
                muted=False,  # Let RealtimeTTS handle playback directly
                language='it',  # Default language for sentence splitting
                tokenizer='nltk',  # Use NLTK for fast sentence splitting
                level=30,  # WARNING level logging (less verbose)
            )
            
            # Playback queue and state
            self.text_queue = queue.Queue()  # Queue for incoming text chunks
            self.shutdown_flag = threading.Event()
            self.generator_active = threading.Event()  # Signal when generator should run
            self.last_text_hash = ""
            self.last_translation_time = None
            self.last_tts_received_time = None
            self.is_playing = False
            
            # Start background playback thread with continuous generator
            self.playback_thread = threading.Thread(
                target=self._continuous_playback_worker, 
                daemon=True, 
                name=f"{label}_playback"
            )
            self.playback_thread.start()
            
            logger.info(f"{self.label} RealtimeTTS initialized with {voice_name} @ {speech_rate}")
            
        except Exception as e:
            logger.error(f"{self.label} RealtimeTTS init failed: {e}")
            raise
    
    def _on_stream_start(self):
        """Callback when audio streaming starts."""
        self.is_playing = True
        if self.on_playback_start:
            self.on_playback_start()
        logger.debug(f"{self.label} RealtimeTTS stream started")
    
    def _on_stream_stop(self):
        """Callback when audio streaming stops."""
        self.is_playing = False
        logger.debug(f"{self.label} RealtimeTTS stream stopped")
    
    def speak(self, text):
        """
        Synthesize and queue text for playback with deduplication.
        
        Uses RealtimeTTS streaming for ultra-low latency. Starts speaking
        before full synthesis is complete. Deduplicates identical consecutive texts.
        
        Note: RealtimeTTS handles rate/pitch internally via AzureEngine config,
        so we send plain text (not SSML).
        
        Args:
            text (str): Plain text to synthesize and speak
            
        Returns:
            bool: True if synthesis started, False if text was duplicate
        """
        if not text or not text.strip():
            return False
        
        # Deduplication: skip if same as last text
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash == self.last_text_hash:
            logger.debug(f"{self.label} Skipping duplicate text")
            return False
        
        self.last_text_hash = text_hash
        
        # Log timing: TTS received text
        self.last_tts_received_time = log_timing(
            logger, f"{self.label}-TTS", "TTS RECEIVED TEXT",
            self.last_translation_time,
            f"Text: '{text[:50]}...'" if len(text) > 50 else f"Text: '{text}'"
        )
        
        # Queue plain text for synthesis and playback (RealtimeTTS handles SSML internally)
        self.text_queue.put((text, self.last_tts_received_time))
        self.generator_active.set()  # Signal generator to continue
        
        return True
    
    def _text_generator(self):
        """
        Generator that yields text chunks continuously as they arrive.
        
        ENHANCED FOR MAXIMUM FLUIDITY:
        - Pre-buffering: Waits for multiple chunks before starting
        - Smart concatenation: Joins chunks with proper spacing
        - Continuous flow: Keeps streaming until queue is empty AND no new data for 0.3s
        """
        logger.debug(f"{self.label} Text generator started")
        
        while not self.shutdown_flag.is_set():
            try:
                # Wait for signal that text is available
                if not self.generator_active.wait(timeout=0.1):
                    continue
                
                # ⚡ ADAPTIVE PRE-BUFFERING: 
                # - First chunk: NO pre-buffer (instant start for low latency!)
                # - Subsequent chunks: Pre-buffer for batching (smooth continuation)
                if self.is_first_chunk:
                    # First chunk - yield immediately for minimum latency!
                    logger.debug(f"{self.label} First chunk - skipping pre-buffer for instant start")
                    self.is_first_chunk = False
                else:
                    # Subsequent chunks - pre-buffer for smoother batching
                    if self.pre_buffer_ms_later > 0:
                        time.sleep(self.pre_buffer_ms_later / 1000.0)
                
                chunks_yielded = 0
                idle_cycles = 0
                max_idle_cycles = self.lookahead_cycles  # Configurable lookahead time
                
                # Keep yielding while there's data OR we're still receiving
                while not self.shutdown_flag.is_set():
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
                            idle_cycles = 0  # Reset idle counter
                            
                        except queue.Empty:
                            break
                    
                    # If we got chunks, prepare to yield them
                    if chunk_batch:
                        # BATCHING STRATEGY for smoother playback:
                        # Wait for at least 2 chunks UNLESS:
                        # - It's the first batch (need instant response!)
                        # - We've waited too long already (timeout)
                        # - Queue is clearly empty and not receiving more
                        
                        is_first_batch = (chunks_yielded == 0)
                        has_enough_chunks = (len(chunk_batch) >= 2)
                        waited_too_long = (idle_cycles >= 3)  # 150ms total wait (3 × 50ms)
                        
                        # If we should wait for more chunks
                        if not is_first_batch and not has_enough_chunks and not waited_too_long:
                            # Check if more data is coming
                            if not self.text_queue.empty():
                                # Data in queue, collect more
                                time.sleep(0.01)
                                continue
                            else:
                                # Queue empty, but wait briefly in case more is coming
                                time.sleep(0.01)
                                idle_cycles += 1
                                continue
                        
                        # 🔗 Smart concatenation: Join multiple chunks with space
                        if len(chunk_batch) > 1:
                            combined_text = " ".join(chunk_batch)
                            logger.info(f"{self.label} 🎯 Yielding BATCH ({len(chunk_batch)} chunks): '{combined_text[:60]}...'")
                            yield combined_text
                            chunks_yielded += len(chunk_batch)
                        else:
                            logger.info(f"{self.label} 🎯 Yielding: '{chunk_batch[0][:50]}...'")
                            yield chunk_batch[0]
                            chunks_yielded += 1
                    else:
                        # No chunks available, check if we should keep waiting
                        idle_cycles += 1
                        if idle_cycles >= max_idle_cycles:
                            # Been idle too long, end this generation cycle
                            logger.debug(f"{self.label} Generator idle, ending cycle (yielded {chunks_yielded} chunks)")
                            break
                        
                        # Brief wait before checking again
                        time.sleep(0.01)
                
                # Clear signal when done with this cycle
                if self.text_queue.empty():
                    self.generator_active.clear()
                    
            except Exception as e:
                logger.error(f"{self.label} Generator error: {e}")
                break
        
        logger.debug(f"{self.label} Text generator stopped")
    
    def _continuous_playback_worker(self):
        """
        Background worker with continuous generator for seamless audio.
        
        Uses a generator pattern that feeds RealtimeTTS continuously,
        eliminating gaps between chunks. The generator yields text as it
        arrives in the queue, creating a seamless audio stream.
        """
        logger.info(f"{self.label} RealtimeTTS continuous playback worker started")
        
        while not self.shutdown_flag.is_set():
            try:
                # Wait for first text to arrive
                if not self.generator_active.wait(timeout=1.0):
                    continue
                
                logger.info(f"{self.label} ⚡ Starting continuous playback session")
                
                # Reset first chunk flag for this session (for adaptive behavior)
                self.is_first_chunk = True
                
                # Record start time for timing
                session_start = time.time()
                
                try:
                    # Feed the generator to stream and play continuously
                    # This eliminates gaps between chunks!
                    self.stream.feed(self._text_generator())
                    self.stream.play(
                        # ⚡ SEAMLESS MODE: Optimized for continuous streaming with smooth transitions
                        fast_sentence_fragment=True,
                        fast_sentence_fragment_allsentences=True,
                        fast_sentence_fragment_allsentences_multiple=True,
                        
                        # Buffer & Fragment settings - OPTIMIZED FOR LOW LATENCY!
                        buffer_threshold_seconds=self.buffer_threshold,  # 0ms for instant start!
                        minimum_sentence_length=self.min_sentence_length,  # No minimum (0)
                        minimum_first_fragment_length=self.min_first_fragment_length,  # Instant (0)
                        context_size=self.context_size,  # Extended context (15 words)
                        force_first_fragment_after_words=self.force_first_fragment_after_words,  # Early (4 words)
                        
                        log_synthesized_text=False,  # Reduced logging for performance
                        muted=False,
                    )
                    
                    # Log session completion
                    session_duration = (time.time() - session_start) * 1000
                    logger.info(f"{self.label} 🏁 Playback session complete: {session_duration:.1f}ms")
                    
                except Exception as e:
                    logger.error(f"{self.label} Continuous playback error: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    
                # NO PAUSE between sessions for maximum fluidity!
                # The generator will naturally wait for new data
                
            except Exception as e:
                logger.error(f"{self.label} Playback worker error: {e}")
        
        logger.info(f"{self.label} RealtimeTTS continuous playback worker stopped")
    
    def stop_playback(self):
        """Stop current audio playback immediately."""
        try:
            self.stream.stop()
            logger.info(f"{self.label} Playback stopped")
        except Exception as e:
            logger.error(f"{self.label} Stop playback error: {e}")
    
    def clear_queue(self):
        """Clear all pending synthesis tasks from queue."""
        cleared = 0
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        
        if cleared > 0:
            logger.info(f"{self.label} Cleared {cleared} pending items from queue")
    
    def shutdown(self):
        """
        Shutdown TTS service and cleanup resources.
        
        Stops playback, clears queue, shuts down RealtimeTTS engine,
        and terminates background threads.
        """
        try:
            logger.info(f"{self.label} Shutting down RealtimeTTS...")
            
            self.shutdown_flag.set()
            self.generator_active.set()  # Wake up generator/worker
            self.stop_playback()
            self.clear_queue()
            
            # Shutdown RealtimeTTS engine
            if hasattr(self, 'stream'):
                self.stream.stop()
            
            if hasattr(self, 'engine'):
                self.engine.shutdown()
            
            # Wait for playback thread
            if self.playback_thread.is_alive():
                self.playback_thread.join(timeout=2.0)
            
            logger.info(f"{self.label} RealtimeTTS shutdown complete")
            
        except Exception as e:
            logger.error(f"{self.label} Shutdown error: {e}")
