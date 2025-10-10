"""
Voice Activity Detection (VAD) implementation.

This module provides energy-based voice activity detection with adaptive
thresholding and noise floor estimation.
"""

import time
import numpy as np
from config.settings import ENERGY_THRESHOLD, HANGOVER_MS, SAMPLE_RATE


class EnergyVAD:
    """
    Energy-based Voice Activity Detector with adaptive thresholding.
    
    Detects speech activity in audio frames using RMS energy calculation
    with moving average smoothing and adaptive threshold adjustment based
    on estimated noise floor.
    
    Attributes:
        threshold (float): Initial energy threshold for voice detection
        hangover_ms (int): Milliseconds to wait before marking speech as ended
        speaking (bool): Current speech activity state
        last_voice_ms (float): Timestamp of last detected voice activity
        energy_buffer (list): Recent energy values for smoothing
        buffer_size (int): Size of energy buffer for moving average
        adaptive_threshold (float): Current adaptive threshold value
        noise_floor (float): Estimated background noise level
    """
    
    def __init__(self, threshold=ENERGY_THRESHOLD, hangover_ms=HANGOVER_MS):
        """
        Initialize VAD with specified parameters.
        
        Args:
            threshold (float, optional): Energy threshold for voice detection
            hangover_ms (int, optional): Hangover time in milliseconds
        """
        self.threshold = threshold
        self.hangover_ms = hangover_ms
        self.speaking = False
        self.last_voice_ms = 0.0
        self.energy_buffer = []
        self.buffer_size = 5
        self.adaptive_threshold = threshold
        self.noise_floor = threshold * 0.5
    
    def process_frame(self, pcm16_bytes, sample_rate=SAMPLE_RATE):
        """
        Process audio frame and detect voice activity.
        
        Calculates RMS energy of the frame, updates adaptive threshold,
        and determines if frame contains speech based on hangover logic.
        
        Args:
            pcm16_bytes (bytes): 16-bit PCM audio data
            sample_rate (int, optional): Audio sample rate in Hz
            
        Returns:
            tuple: (state_changed, is_speaking, energy)
                - state_changed (bool): True if speaking state changed this frame
                - is_speaking (bool): Current speaking state
                - energy (float): Calculated frame energy
        """
        try:
            # Convert PCM16 bytes to float32 normalized array
            audio_data = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32767.0
            
            if len(audio_data) == 0:
                return (False, self.speaking, 0.0)
            
            # Calculate RMS energy
            rms_energy = float(np.sqrt(np.mean(audio_data ** 2)))
            
            # Update energy buffer for moving average
            self.energy_buffer.append(rms_energy)
            if len(self.energy_buffer) > self.buffer_size:
                self.energy_buffer.pop(0)
            
            # Calculate average energy
            avg_energy = np.mean(self.energy_buffer)
            
            # Update noise floor estimate when not speaking
            if not self.speaking and avg_energy < self.adaptive_threshold:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * avg_energy
                self.adaptive_threshold = max(self.threshold, self.noise_floor * 2.0)
            
            current_time_ms = time.time() * 1000
            voice_detected = (avg_energy >= self.adaptive_threshold)
            state_changed = False
            
            if voice_detected:
                # Voice detected in this frame
                self.last_voice_ms = current_time_ms
                if not self.speaking:
                    # Transition to speaking state
                    self.speaking = True
                    state_changed = True
            else:
                # No voice in this frame
                if self.speaking and (current_time_ms - self.last_voice_ms) > self.hangover_ms:
                    # Hangover period expired, stop speaking
                    self.speaking = False
                    state_changed = True
            
            return (state_changed, self.speaking, avg_energy)
            
        except Exception:
            return (False, self.speaking, 0.0)


def create_vad():
    """
    Factory function to create VAD instance with default settings.
    
    Returns:
        EnergyVAD: Configured VAD instance
    """
    return EnergyVAD()
