"""Audio playback helpers."""

from io import BytesIO
import soundfile as sf
import sounddevice as sd


def play_wav_bytes(wav_bytes: bytes, device_index=None):
    """Play WAV bytes using sounddevice.

    Args:
        wav_bytes: WAV-encoded audio bytes.
        device_index: Optional output device index; None uses default.
    """
    with BytesIO(wav_bytes) as bio:
        data, samplerate = sf.read(bio, dtype="float32", always_2d=False)
        sd.play(data, samplerate, device=device_index, blocking=True)
