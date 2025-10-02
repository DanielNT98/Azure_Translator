import threading
import numpy as np
import sounddevice as sd


def start_capture_thread(b_cap_idx, push_stream_down, logger, silence_gate: float = 1e-7):
    stop_event = threading.Event()

    def _run():
        samplerate = 16000
        channels = 1
        dtype = "int16"
        frames_per_chunk = int(samplerate * 0.1)  # 100ms
        try:
            with sd.InputStream(samplerate=samplerate, channels=channels, dtype=dtype, device=b_cap_idx) as stream:
                logger.info("[DOWN CAPTURE] Avviata cattura da CABLE-B Output")
                while not stop_event.is_set():
                    data, overflowed = stream.read(frames_per_chunk)
                    if overflowed:
                        logger.warning("[DOWN CAPTURE] Overflow buffer input")
                    # gate anti-silenzio
                    if np.mean(data.astype(np.float32) ** 2) < silence_gate:
                        continue
                    push_stream_down.write(data.tobytes())
        except Exception as e:
            logger.exception(f"[DOWN CAPTURE] Errore: {e}")
        finally:
            try:
                push_stream_down.close()
            except Exception:
                pass
            logger.info("[DOWN CAPTURE] Terminata")

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return stop_event, th
