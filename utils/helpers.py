"""Utility functions and helpers."""

import wave
import io
import numpy as np


def wav_bytes_to_float32(wav_bytes):
    """
    Convert WAV audio bytes to float32 numpy array.
    
    Converts 16-bit PCM WAV data to normalized float32 format suitable
    for audio processing. Automatically handles stereo to mono conversion.
    
    Args:
        wav_bytes (bytes): Raw WAV file bytes
        
    Returns:
        tuple: (audio_data, sample_rate)
            - audio_data (np.ndarray): Normalized float32 audio data [-1, 1]
            - sample_rate (int): Sample rate in Hz
            
    Raises:
        Exception: If WAV file cannot be parsed
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
            sample_rate = wav_file.getframerate()
            num_frames = wav_file.getnframes()
            audio_bytes = wav_file.readframes(num_frames)
            
            # Convert to float32 normalized to [-1, 1]
            audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
            
            # Convert stereo to mono if needed
            if wav_file.getnchannels() > 1:
                audio_data = audio_data.reshape(-1, wav_file.getnchannels())[:, 0]
            
            return audio_data, sample_rate
    except Exception as e:
        raise Exception(f"Error converting WAV to float32: {e}")
