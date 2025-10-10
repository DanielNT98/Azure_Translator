"""
Audio capture with pass-through and VAD integration.

This module handles real-time audio capture from input devices with optional
pass-through mixing, call center mode keyboard sounds, and Voice Activity Detection.
"""

import logging
import os
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from core.vad import create_vad
from config.settings import SAMPLE_RATE, CHANNELS, FRAME_SAMPLES


logger = logging.getLogger("translator_bridge")


class CapturePipe:
    """
    Audio capture pipeline with pass-through and VAD.
    
    Captures audio from input device, processes through VAD, optionally
    mixes with pass-through audio or keyboard sounds, and forwards to
    translation service.
    
    Attributes:
        in_dev_idx (int): Input device index
        push_stream: Azure push stream (for Azure mode) or None (for DeepL mode)
        channel (str): Channel identifier ("A" or "B")
        label (str): Label for logging
        vad (EnergyVAD): Voice activity detector
        stream (sd.InputStream): Active input stream
        running (bool): Capture running state
        buf (bytes): Audio buffer for frame assembly
        passthrough_device (int or None): Output device for pass-through
        passthrough_volume (float): Pass-through volume [0-1]
        passthrough_always (bool): True for continuous pass-through
        passthrough_buffer (queue.Queue): Queue for pass-through audio
        passthrough_thread (threading.Thread): Pass-through worker thread
        passthrough_running (bool): Pass-through thread running state
        call_center_mode (bool): Enable keyboard sound mode
        keyboard_audio (np.ndarray): Loaded keyboard sound audio
        keyboard_audio_pos (int): Current position in keyboard audio loop
        keyboard_playing (bool): Keyboard sound playback state
        last_speech_state (bool): Last VAD speech state
        keyboard_stop_event (threading.Event): Event to stop keyboard sound
        other_capture_pipe (CapturePipe): Reference to other channel pipe
        deepl_translator (DeepLTranslator): Reference to DeepL translator (DeepL mode)
        azure_translator (AzureTranslator): Reference to Azure translator (Azure mode)
    """
    
    def __init__(self, input_device_idx, push_stream, channel, label, vad=None,
                 passthrough_device=None, passthrough_volume=0.5,
                 passthrough_always=False, call_center_mode=False,
                 keyboard_sound_file=None, deepl_translator=None, turn_manager=None):
        """
        Initialize capture pipe with optional pass-through and keyboard mode.
        
        Args:
            input_device_idx (int): Input device index
            push_stream: Azure push stream (for Azure mode) or None (for DeepL mode)
            channel (str): Channel identifier ("A" or "B")
            label (str): Label for logging
            vad (EnergyVAD, optional): VAD instance or None to create new
            passthrough_device (int, optional): Output device for pass-through
            passthrough_volume (float, optional): Pass-through volume. Defaults to 0.5.
            passthrough_always (bool, optional): Continuous pass-through. Defaults to False.
            call_center_mode (bool, optional): Enable keyboard sounds. Defaults to False.
            keyboard_sound_file (str, optional): Path to keyboard sound WAV file
            deepl_translator (DeepLTranslator, optional): Translator reference (for DeepL mode)
            turn_manager (TurnManager, optional): Turn manager for channel priority
        """
        self.in_dev_idx = input_device_idx
        self.push_stream = push_stream
        self.channel = channel
        self.label = label
        self.vad = vad if vad is not None else create_vad()
        self.stream = None
        self.running = False
        self.buf = b""
        self.turn_manager = turn_manager
        
        # Translator references (mutually exclusive in practice)
        self.deepl_translator = deepl_translator  # For DeepL mode
        self.azure_translator = None  # For Azure mode (set externally)
        
        # Pass-through configuration
        self.passthrough_device = passthrough_device
        self.passthrough_volume = passthrough_volume
        self.passthrough_always = passthrough_always
        self.passthrough_buffer = queue.Queue(maxsize=500)
        self.passthrough_thread = None
        self.passthrough_running = False
        
        # Call center mode configuration
        self.call_center_mode = call_center_mode
        self.keyboard_audio = None
        self.keyboard_audio_pos = 0
        self.keyboard_playing = False
        self.last_speech_state = False
        self.keyboard_stop_event = threading.Event()
        
        # References
        self.other_capture_pipe = None
        self.deepl_translator = deepl_translator
        
        # Load keyboard sound if in call center mode
        if self.call_center_mode and keyboard_sound_file:
            self._load_keyboard_sound(keyboard_sound_file)
    
    def _load_keyboard_sound(self, filepath):
        """
        Load keyboard sound WAV file with resampling.
        
        Loads WAV file, resamples if needed, and normalizes to float32 format.
        
        Args:
            filepath (str): Path to keyboard sound WAV file
        """
        try:
            if not os.path.exists(filepath):
                logger.warning(f"{self.label} Keyboard sound file not found: {filepath}")
                return
            
            with wave.open(filepath, 'rb') as wav:
                original_rate = wav.getframerate()
                num_frames = wav.getnframes()
                audio_bytes = wav.readframes(num_frames)
                
                # Convert to float32 normalized [-1, 1]
                audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                
                # Convert stereo to mono if needed
                if wav.getnchannels() > 1:
                    audio_data = audio_data.reshape(-1, wav.getnchannels())[:, 0]
                
                # Resample if needed
                if original_rate != SAMPLE_RATE:
                    try:
                        from scipy import signal
                        num_samples = int(len(audio_data) * SAMPLE_RATE / original_rate)
                        audio_data = signal.resample(audio_data, num_samples)
                        logger.info(f"{self.label} Keyboard sound resampled {original_rate}Hz -> {SAMPLE_RATE}Hz")
                    except ImportError:
                        logger.warning(f"{self.label} scipy not available, using original sample rate")
                
                self.keyboard_audio = audio_data
                logger.info(f"{self.label} Keyboard sound loaded: {len(audio_data)} samples")
        
        except Exception as e:
            logger.error(f"{self.label} Error loading keyboard sound: {e}")
            self.keyboard_audio = None
    
    def _get_keyboard_chunk(self, num_samples):
        """
        Get chunk of keyboard sound with looping.
        
        Args:
            num_samples (int): Number of samples to return
            
        Returns:
            np.ndarray: Audio chunk (silence if keyboard audio not loaded)
        """
        if self.keyboard_audio is None or len(self.keyboard_audio) == 0:
            return np.zeros(num_samples, dtype=np.float32)
        
        chunk = np.zeros(num_samples, dtype=np.float32)
        remaining = num_samples
        offset = 0
        
        while remaining > 0:
            available = len(self.keyboard_audio) - self.keyboard_audio_pos
            to_copy = min(remaining, available)
            
            chunk[offset:offset+to_copy] = self.keyboard_audio[
                self.keyboard_audio_pos:self.keyboard_audio_pos+to_copy
            ]
            
            self.keyboard_audio_pos += to_copy
            if self.keyboard_audio_pos >= len(self.keyboard_audio):
                self.keyboard_audio_pos = 0  # Loop
            
            offset += to_copy
            remaining -= to_copy
        
        return chunk
    
    def _cb(self, indata, frames, time_info, status):
        """
        Audio input callback.
        
        Processes incoming audio frames, runs VAD, handles pass-through,
        and forwards audio to translator.
        
        Args:
            indata (np.ndarray): Input audio data
            frames (int): Number of frames
            time_info: Timing information
            status: Stream status
        """
        if not self.running:
            return
        
        try:
            # Convert to PCM16 bytes
            pcm16 = (indata[:, 0] * 32767.0).astype(np.int16).tobytes()
            self.buf += pcm16
            frame_bytes = 2 * FRAME_SAMPLES
            
            while len(self.buf) >= frame_bytes:
                frame = self.buf[:frame_bytes]
                self.buf = self.buf[frame_bytes:]
                
                # Run VAD
                state_changed, is_speaking, energy = self.vad.process_frame(frame)
                
                # Log speech state changes
                if state_changed:
                    if is_speaking:
                        logger.info(f"{self.label} SPEECH START")
                        
                        # Log timing for speech start
                        from utils.timing import log_timing
                        ts_speech_start = log_timing(
                            logger, self.label.replace("[", "").replace("]", ""), 
                            "SPEECH START", None, f"Energy: {energy:.4f}"
                        )
                        
                        # Store timestamp in translator for timing tracking
                        if self.deepl_translator:
                            self.deepl_translator.last_speech_start_time = ts_speech_start
                        
                        # Update turn manager if available
                        if self.turn_manager:
                            self.turn_manager.update_activity(self.channel, True, energy)
                        
                        # Start keyboard sound in call center mode
                        if self.call_center_mode and self.keyboard_audio is not None:
                            self.keyboard_stop_event.clear()
                            self.keyboard_playing = True
                            threading.Thread(
                                target=self._play_keyboard_async,
                                daemon=True
                            ).start()
                    else:
                        logger.info(f"{self.label} SPEECH END")
                
                self.last_speech_state = is_speaking
                
                # Forward audio to translator
                # For DeepL: push to translator (continuous stream)
                # For Azure: write to push stream
                if self.deepl_translator:
                    self.deepl_translator.push_audio(frame)
                elif self.push_stream:
                    self.push_stream.write(frame)
                
                # Handle pass-through
                if self.passthrough_device is not None:
                    should_passthrough = self.passthrough_always or is_speaking
                    
                    if should_passthrough:
                        # Convert PCM16 to float32
                        audio_float = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32767.0
                        
                        # In call center mode, replace voice with keyboard sound
                        if self.call_center_mode and is_speaking and self.keyboard_audio is not None:
                            keyboard_chunk = self._get_keyboard_chunk(len(audio_float))
                            passthrough_audio = keyboard_chunk * self.passthrough_volume
                        else:
                            passthrough_audio = audio_float * self.passthrough_volume
                        
                        # Queue for pass-through
                        try:
                            self.passthrough_buffer.put_nowait(passthrough_audio)
                        except queue.Full:
                            pass  # Drop if queue full
        
        except Exception as e:
            logger.error(f"{self.label} Callback error: {e}")
    
    def _play_keyboard_async(self):
        """Play keyboard sound asynchronously with interruption support."""
        try:
            if self.keyboard_audio is None:
                return
            
            chunk_size = FRAME_SAMPLES
            total_samples = len(self.keyboard_audio)
            pos = 0
            
            while pos < total_samples and not self.keyboard_stop_event.is_set():
                chunk = self.keyboard_audio[pos:pos+chunk_size]
                if len(chunk) > 0:
                    try:
                        self.passthrough_buffer.put(
                            chunk * self.passthrough_volume,
                            timeout=0.1
                        )
                    except queue.Full:
                        pass
                pos += chunk_size
            
            if self.keyboard_stop_event.is_set():
                logger.info(f"{self.label} Keyboard sound interrupted")
            
            self.keyboard_playing = False
        
        except Exception as e:
            logger.error(f"{self.label} Keyboard playback error: {e}")
    
    def _passthrough_worker(self):
        """
        Background thread for pass-through audio playback.
        
        Continuously reads from pass-through buffer and plays through
        output device with configurable volume.
        """
        stream = None
        frame_count = 0
        dropped_frames = 0
        
        try:
            # Larger blocksize for stability
            blocksize = FRAME_SAMPLES * 4
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                device=self.passthrough_device,
                dtype='float32',
                blocksize=blocksize,
                latency='low'
            )
            stream.start()
            
            mode = "CONTINUOUS" if self.passthrough_always else "VAD"
            if self.call_center_mode and self.keyboard_audio is not None:
                logger.info(f"{self.label} Pass-through active [KEYBOARD] (volume: {self.passthrough_volume*100:.0f}%)")
            else:
                logger.info(f"{self.label} Pass-through active [{mode}] (volume: {self.passthrough_volume*100:.0f}%)")
            
            logger.info(f"{self.label} Pass-through device idx: {self.passthrough_device}, blocksize: {blocksize}")
            
            # Accumulate chunks for larger writes
            accumulated = []
            accumulated_samples = 0
            
            while self.passthrough_running:
                try:
                    chunk = self.passthrough_buffer.get(timeout=0.1)
                    accumulated.append(chunk)
                    accumulated_samples += len(chunk)
                    
                    # Write when accumulated enough samples
                    if accumulated_samples >= blocksize:
                        combined = np.concatenate(accumulated)
                        stream.write(combined[:blocksize].reshape(-1, 1))
                        frame_count += 1
                        
                        # Keep remainder for next write
                        if len(combined) > blocksize:
                            accumulated = [combined[blocksize:]]
                            accumulated_samples = len(combined) - blocksize
                        else:
                            accumulated = []
                            accumulated_samples = 0
                
                except queue.Empty:
                    # Write any remaining accumulated audio
                    if accumulated:
                        combined = np.concatenate(accumulated)
                        stream.write(combined.reshape(-1, 1))
                        frame_count += 1
                        accumulated = []
                        accumulated_samples = 0
                    continue
        
        finally:
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except:
                    pass
            logger.info(f"{self.label} Pass-through ended (total frames: {frame_count})")
    
    def start(self):
        """
        Start audio capture and pass-through.
        
        Opens input stream and starts pass-through thread if configured.
        
        Raises:
            Exception: If audio device initialization fails
        """
        try:
            if self.in_dev_idx is None:
                raise ValueError(f"{self.label} Input device not configured")
            
            self.running = True
            
            # Start pass-through thread if configured
            if self.passthrough_device is not None:
                self.passthrough_running = True
                self.passthrough_thread = threading.Thread(
                    target=self._passthrough_worker,
                    daemon=True
                )
                self.passthrough_thread.start()
            
            # Start input stream
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=FRAME_SAMPLES,
                device=self.in_dev_idx,
                callback=self._cb,
                latency='low'
            )
            self.stream.start()
            logger.info(f"{self.label} Capture started")
        
        except Exception as e:
            logger.error(f"{self.label} Start error: {e}")
            raise
    
    def stop(self):
        """
        Stop audio capture and pass-through.
        
        Closes input stream and stops pass-through thread.
        """
        try:
            self.running = False
            self.passthrough_running = False
            
            if self.passthrough_thread:
                self.passthrough_thread.join(timeout=1.0)
            
            if self.stream:
                self.stream.stop()
                self.stream.close()
        
        except Exception as e:
            logger.error(f"{self.label} Stop error: {e}")
