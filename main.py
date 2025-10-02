import time

from translator_bidir.config import load_config, setup_logger
from translator_bidir.devices import resolve_devices
from translator_bidir.azure_clients import create_upstream_clients, create_downstream_clients
from translator_bidir.workers import TtsWorker
from translator_bidir.capture import start_capture_thread
from translator_bidir.handlers import (
    make_up_recognized_handler,
    make_down_recognized_handler,
    on_cancelled,
    on_session,
)


def print_instructions():
    print("\n=== ISTRUZIONI ===")
    print("In Teams seleziona:")
    print("  - Microfono  : 'CABLE Output' (cavo A)")
    print("  - Altoparlante: 'CABLE-B Input' (cavo B)")
    print("La tua traduzione va su CABLE Input (A) -> Teams sente solo quella.")
    print("Loro vengono catturati da CABLE-B Output (B), tradotti e riprodotti sulle tue cuffie.")
    print("Ctrl+C per terminare.\n")


def main():
    cfg = load_config()
    logger = setup_logger(cfg.LOG_PATH)

    logger.info(
        f"Region={cfg.SPEECH_REGION} | Up: {cfg.SRC_LANG}->{cfg.TGT_LANG} ({cfg.VOICE_NAME}) | Down: {cfg.DOWN_SRC_LANG}->{cfg.DOWN_TGT_LANG} ({cfg.DOWN_VOICE_NAME})"
    )

    dev = resolve_devices(cfg, logger)
    a_play_idx = dev["a_play_idx"]
    b_cap_idx = dev["b_cap_idx"]
    hp_play_idx = dev["hp_play_idx"]

    recognizer_up, synth_up = create_upstream_clients(cfg)
    recognizer_down, synth_down, push_stream_down = create_downstream_clients(cfg)

    up_worker = TtsWorker("UP", synth_up, a_play_idx, logger)
    down_worker = TtsWorker("DOWN", synth_down, hp_play_idx, logger)
    up_worker.start()
    down_worker.start()

    cap_stop, cap_thread = start_capture_thread(b_cap_idx, push_stream_down, logger)

    recognizer_up.recognized.connect(make_up_recognized_handler(cfg, up_worker, logger))
    recognizer_up.canceled.connect(on_cancelled("UP", logger))
    recognizer_up.session_started.connect(on_session("UP", True, logger))
    recognizer_up.session_stopped.connect(on_session("UP", False, logger))

    recognizer_down.recognized.connect(make_down_recognized_handler(cfg, down_worker, logger))
    recognizer_down.canceled.connect(on_cancelled("DOWN", logger))
    recognizer_down.session_started.connect(on_session("DOWN", True, logger))
    recognizer_down.session_stopped.connect(on_session("DOWN", False, logger))

    print_instructions()

    recognizer_up.start_continuous_recognition()
    recognizer_down.start_continuous_recognition()

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nInterruzione…")
    finally:
        recognizer_up.stop_continuous_recognition()
        recognizer_down.stop_continuous_recognition()
        cap_stop.set()
        cap_thread.join(timeout=1.0)
        up_worker.stop()
        down_worker.stop()
        logger.info("=== Arresto Translator Bidir ===")


if __name__ == "__main__":
    main()
