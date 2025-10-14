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
            self.buffer_threshold = 0.0  # Lower = faster start, higher = smoother
            self.min_sentence_length = 10
            self.min_first_fragment_length = 5
            self.context_size = 6  # Reduced from default 12 for faster boundary detection
            self.force_first_fragment_after_words = 10  # Force fragment after 10 words
            
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
                pitch=0.1,  # Normal pitch (no modulation)
                audio_format="riff-16khz-16bit-mono-pcm",  # 16kHz for low latency
            )
            
            # Create streaming handler with optimized settings for low latency
            self.stream = TextToAudioStream(
                engine=self.engine,
                on_audio_stream_start=self._on_stream_start,
                on_audio_stream_stop=self._on_stream_stop,
                output_device_index=out_dev_idx,
                muted=False,  # Let RealtimeTTS handle playback directly
                language='en',  # Default language for sentence splitting
                tokenizer='nltk',  # Use NLTK for fast sentence splitting
                level=30,  # WARNING level logging (less verbose)
            )
            
            # Playback queue and state
            self.play_queue = queue.Queue()
            self.shutdown_flag = threading.Event()
            self.last_text_hash = ""
            self.last_translation_time = None
            self.last_tts_received_time = None
            self.is_playing = False
            
            # Start background playback thread
            self.playback_thread = threading.Thread(
                target=self._playback_worker, 
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
        self.play_queue.put(("text", text, text, self.last_tts_received_time))
        
        return True
    
    def _playback_worker(self):
        """
        Background worker for audio playback using RealtimeTTS streaming.
        
        Continuously processes the play queue, synthesizing and playing audio
        with minimal latency. Uses RealtimeTTS for streaming synthesis.
        
        If new text arrives while playing, stops current playback and switches
        to the new text immediately (low-latency interrupt mode).
        """
        logger.info(f"{self.label} RealtimeTTS playback worker started")
        
        while not self.shutdown_flag.is_set():
            try:
                # Get next item from queue with timeout
                try:
                    item = self.play_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                if item is None:
                    break
                
                # ⚡ SKIP OLD ITEMS: If multiple items queued, skip to the latest one
                queued_count = 0
                while not self.play_queue.empty():
                    try:
                        # Peek at queue size
                        queued_count = self.play_queue.qsize()
                        if queued_count > 0:
                            logger.debug(f"{self.label} 🔄 Skipping old text ({queued_count} newer items in queue)")
                            # Get the latest item, discarding current one
                            item = self.play_queue.get_nowait()
                    except queue.Empty:
                        break
                
                item_type, ssml_text, original_text, received_time = item
                
                if item_type != "text":
                    continue
                
                # Note: ssml_text is actually plain text now (RealtimeTTS doesn't need SSML)
                plain_text = ssml_text
                
                # Start timing for synthesis and playback
                synth_start = time.time()
                
                try:
                    # Feed text to stream and play (RealtimeTTS handles synthesis + playback)
                    log_timing(
                        logger, f"{self.label}-TTS", "TTS SYNTHESIS START",
                        received_time,
                        f"Text: '{original_text[:30]}...'" if len(original_text) > 30 else f"Text: '{original_text}'"
                    )
                    
                    # RealtimeTTS play() method handles everything internally
                    # Starts synthesis immediately and streams audio as it's ready
                    self.stream.feed(plain_text)
                    self.stream.play(
                        fast_sentence_fragment=True,  # Prioritize speed for first fragment
                        fast_sentence_fragment_allsentences=True,  # Apply fast processing to ALL sentences
                        
                        buffer_threshold_seconds=self.buffer_threshold,  # Configurable buffer
                        minimum_sentence_length=self.min_sentence_length,
                        minimum_first_fragment_length=self.min_first_fragment_length,
                        context_size=self.context_size,  # Faster boundary detection
                        force_first_fragment_after_words=self.force_first_fragment_after_words,  # Force after N words
                        log_synthesized_text=True,    # Disable logging for performance
                        muted=False,                   # Enable audio playback
                    )  # Blocks until playback complete
                    
                    # Log completion
                    playback_end = time.time()
                    total_duration = (playback_end - synth_start) * 1000
                    
                    log_timing(
                        logger, f"{self.label}-TTS", "TTS + PLAYBACK COMPLETE",
                        received_time,
                        f"Total: {total_duration:.1f}ms"
                    )
                    
                except Exception as e:
                    logger.error(f"{self.label} RealtimeTTS synthesis error: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                
            except Exception as e:
                logger.error(f"{self.label} Playback worker error: {e}")
        
        logger.info(f"{self.label} RealtimeTTS playback worker stopped")
    
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
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
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
            self.stop_playback()
            self.clear_queue()
            
            # Signal playback thread to stop
            self.play_queue.put(None)
            
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
