"""
Azure Text-to-Speech service with streaming synthesis.

This module provides fast streaming TTS using Azure Cognitive Services
with configurable speech rate, voice selection, and queue-based playback.
"""

import hashlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import azure.cognitiveservices.speech as speechsdk
import numpy as np
import sounddevice as sd

from utils.timing import log_timing
from utils.helpers import wav_bytes_to_float32


logger = logging.getLogger("translator_bridge")

# Thread pool for synthesis tasks
thread_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tts_worker")


class FastStreamingTTS:
    """
    Fast streaming Text-to-Speech using Azure Neural Voices.
    
    Provides low-latency speech synthesis with SSML-based speed control,
    queue-based audio playback, and text deduplication. Synthesizes audio
    in background threads and plays through specified output device.
    
    Attributes:
        synth (speechsdk.SpeechSynthesizer): Azure TTS synthesizer
        play_idx (int or None): Audio output device index
        label (str): Label for logging identification
        speech_rate (str): SSML speech rate (e.g., "+35%")
        voice_name (str): Azure neural voice name
        play_queue (queue.Queue): Queue for synthesized audio playback
        synth_lock (threading.Lock): Lock for thread-safe synthesis
        shutdown_flag (threading.Event): Shutdown signal
        on_playback_start (callable): Callback when playback starts
        playback_thread (threading.Thread): Background playback worker thread
        last_text_hash (str): Hash of last synthesized text (deduplication)
        last_translation_time (float): Timestamp of last translation (for timing)
        last_tts_received_time (float): Timestamp when TTS received text
    """
    
    def __init__(self, speech_key, region, voice_name, out_dev_idx, label, 
                 speech_rate="+25%", on_playback_start=None):
        """
        Initialize Azure TTS service.
        
        Args:
            speech_key (str): Azure Speech Service subscription key
            region (str): Azure region (e.g., "westeurope")
            voice_name (str): Azure neural voice name (e.g., "en-US-GuyNeural")
            out_dev_idx (int or None): Audio output device index
            label (str): Label for logging
            speech_rate (str, optional): SSML speech rate. Defaults to "+25%".
            on_playback_start (callable, optional): Callback when playback starts
            
        Raises:
            Exception: If Azure TTS initialization fails
        """
        try:
            config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
            config.set_property(
                speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, 
                "true"
            )
            config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
            )
            self.synth = speechsdk.SpeechSynthesizer(
                speech_config=config, 
                audio_config=None
            )
        except Exception as e:
            logger.error(f"{label} Initialization error: {e}")
            raise
        
        self.play_idx = out_dev_idx
        self.label = label
        self.speech_rate = speech_rate
        self.voice_name = voice_name
        self.play_queue = queue.Queue(maxsize=5)
        self.synth_lock = threading.Lock()
        self.shutdown_flag = threading.Event()
        self.on_playback_start = on_playback_start
        self.playback_thread = threading.Thread(
            target=self._playback_worker, 
            daemon=True
        )
        self.playback_thread.start()
        self.last_text_hash = ""
        
        # Timing attributes
        self.last_translation_time = None
        self.last_tts_received_time = None
        
        logger.info(f"{label} Speed: {speech_rate}, Voice: {voice_name}")
    
    def stop(self):
        """Clear playback queue without shutting down."""
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
            except queue.Empty:
                break
    
    def shutdown(self):
        """
        Shutdown TTS service and cleanup resources.
        
        Signals shutdown, clears queue, and waits for playback thread.
        """
        self.shutdown_flag.set()
        self.stop()
    
    def speak(self, text):
        """
        Synthesize and speak text asynchronously.
        
        Checks for duplicate text, logs timing, and submits synthesis
        task to thread pool for background processing.
        
        Args:
            text (str): Text to synthesize and speak
        """
        if not text or not text.strip() or self.shutdown_flag.is_set():
            return
        
        text = text.strip()
        
        # Deduplication check
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        if text_hash == self.last_text_hash:
            return
        self.last_text_hash = text_hash
        
        # Log timing: TTS received text
        ts_tts_received = log_timing(
            logger,
            self.label,
            "TTS RECEIVED TEXT",
            getattr(self, 'last_translation_time', None),
            f"Text: '{text[:50]}...'" if len(text) > 50 else f"Text: '{text}'"
        )
        self.last_tts_received_time = ts_tts_received
        
        try:
            thread_pool.submit(self._synthesize_and_queue, text)
        except Exception as e:
            logger.error(f"{self.label} Submit error: {e}")
    
    def _synthesize_and_queue(self, text):
        """
        Synthesize text to audio and queue for playback.
        
        Creates SSML with speed control, calls Azure TTS API, and queues
        resulting audio data for playback worker thread.
        
        Args:
            text (str): Text to synthesize
        """
        try:
            if self.shutdown_flag.is_set():
                return
            
            # Create SSML with speed control
            ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='it-IT'>
                <voice name='{self.voice_name}'>
                    <prosody rate='{self.speech_rate}'>
                        {text}
                    </prosody>
                </voice>
            </speak>"""
            
            # Synthesize audio
            result = None
            try:
                with self.synth_lock:
                    result = self.synth.speak_ssml(ssml)
            except Exception as e:
                logger.error(f"{self.label} Synthesis error: {e}")
                return
            
            if self.shutdown_flag.is_set() or not result:
                return
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                # Log timing: synthesis complete
                log_timing(
                    logger,
                    self.label,
                    "TTS SYNTHESIS COMPLETE",
                    getattr(self, 'last_tts_received_time', None),
                    f"Audio size: {len(result.audio_data)} bytes"
                )
                
                # Convert WAV to float32 and queue for playback
                try:
                    audio_data, sample_rate = wav_bytes_to_float32(result.audio_data)
                    self.play_queue.put(
                        (audio_data, sample_rate, time.time()),
                        timeout=2.0
                    )
                except queue.Full:
                    logger.warning(f"{self.label} Play queue full, dropping audio")
                except Exception as e:
                    logger.error(f"{self.label} Audio conversion error: {e}")
        
        except Exception as e:
            logger.error(f"{self.label} Synthesize error: {e}")
    
    def _playback_worker(self):
        """
        Background worker thread for audio playback.
        
        Continuously reads from playback queue and plays audio through
        specified output device. Runs until shutdown signal received.
        """
        while not self.shutdown_flag.is_set():
            try:
                try:
                    item = self.play_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                
                audio_data, sample_rate, timestamp = item
                
                # Check audio age
                age = time.time() - timestamp
                if age > 3.0:
                    logger.warning(f"{self.label} Dropping old audio ({age:.1f}s old)")
                    continue
                
                try:
                    self._play_audio(audio_data, sample_rate)
                except Exception as e:
                    logger.error(f"{self.label} Playback error: {e}")
            
            except Exception as e:
                logger.error(f"{self.label} Worker error: {e}")
    
    def _play_audio(self, audio_data, sample_rate):
        """
        Play audio data through output device.
        
        Opens audio stream, calls playback start callback, streams audio
        in chunks, and logs playback timing.
        
        Args:
            audio_data (np.ndarray): Float32 audio data [-1, 1]
            sample_rate (int): Audio sample rate in Hz
        """
        stream = None
        try:
            # Log timing: playback start
            ts_playback_start = log_timing(
                logger,
                self.label,
                "AUDIO PLAYBACK START",
                getattr(self, 'last_tts_received_time', None),
                f"Duration: {len(audio_data)/sample_rate:.2f}s"
            )
            
            # Call callback before starting playback
            if self.on_playback_start:
                try:
                    self.on_playback_start()
                except Exception as e:
                    logger.error(f"{self.label} Callback error: {e}")
            
            # Open output stream
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                device=self.play_idx,
                dtype='float32',
                blocksize=256,
                latency='low'
            )
            stream.start()
            
            # Stream audio in chunks
            chunk_size = int(sample_rate * 0.020)  # 20ms chunks
            for i in range(0, len(audio_data), chunk_size):
                if self.shutdown_flag.is_set():
                    break
                chunk = audio_data[i:i+chunk_size]
                stream.write(chunk.reshape(-1, 1))
            
            stream.stop()
            
            # Log timing: playback end
            log_timing(
                logger,
                self.label,
                "AUDIO PLAYBACK END",
                ts_playback_start,
                f"Total playback time"
            )
        
        finally:
            if stream:
                try:
                    stream.close()
                except:
                    pass
