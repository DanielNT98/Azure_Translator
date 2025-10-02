"""Audio capture thread feeding Azure PushAudioInputStream."""

import threading
import numpy as np
import sounddevice as sd


def start_capture_thread(b_cap_idx, push_stream_down, logger, silence_gate: float = 1e-7):
    """Start a background thread capturing from input device into Azure stream.

    Captures 16 kHz mono int16 frames and writes to the provided
    PushAudioInputStream. A simple power-based silence gate avoids sending
    pure silence.

    Args:
        b_cap_idx: Input device index for capture (CABLE-B Output).
        push_stream_down: Azure PushAudioInputStream to write bytes into.
        logger: Logger instance.
        silence_gate: Power threshold to skip near-silence frames.

    Returns:
        (stop_event, thread): Event to stop and the running thread instance.
    """
    stop_event = threading.Event()

    def _run():
        samplerate = 16000
        channels = 1
        dtype = "int16"
        frames_per_chunk = int(samplerate * 0.1)  # 100 ms
        try:
            with sd.InputStream(samplerate=samplerate, channels=channels, dtype=dtype, device=b_cap_idx) as stream:
                logger.info("[DOWN CAPTURE] Started capture from CABLE-B Output")
                while not stop_event.is_set():
                    data, overflowed = stream.read(frames_per_chunk)
                    if overflowed:
                        logger.warning("[DOWN CAPTURE] Input buffer overflow")
                    # simple silence gate
                    if np.mean(data.astype(np.float32) ** 2) < silence_gate:
                        continue
                    push_stream_down.write(data.tobytes())
        except Exception as e:
            logger.exception(f"[DOWN CAPTURE] Error: {e}")
        finally:
            try:
                push_stream_down.close()
            except Exception:
                pass
            logger.info("[DOWN CAPTURE] Stopped")

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return stop_event, th
