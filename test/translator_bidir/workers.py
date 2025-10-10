"""Background workers for text-to-speech synthesis and playback."""

import threading
from queue import Queue, Empty
import azure.cognitiveservices.speech as speechsdk
from .audio import play_wav_bytes


class TtsWorker:
    """Queue-based TTS worker running in a background thread.

    Put text strings with `.put(text)`. The worker synthesizes audio via
    Azure Speech and plays it on the given output device.
    """
    def __init__(self, name: str, synthesizer: speechsdk.SpeechSynthesizer, device_index, logger):
        """Initialize the worker.

        Args:
            name: Label used in logs (e.g., "UP" or "DOWN").
            synthesizer: Azure Speech synthesizer instance.
            device_index: Output device index for playback (or None for default).
            logger: Logger instance.
        """
        self.name = name
        self.synth = synthesizer
        self.device_index = device_index
        self.logger = logger
        self.queue: Queue[str] = Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        """Start the worker thread."""
        self._thread.start()

    def stop(self):
        """Signal the worker to stop and join briefly."""
        self._stop.set()
        self._thread.join(timeout=1.0)

    def put(self, text: str):
        """Enqueue a text snippet for synthesis.

        Args:
            text: Text to synthesize. Empty or None values are ignored.
        """
        if text:
            self.queue.put(text)

    def _run(self):
        """Thread loop: fetch text, synthesize, and play audio."""
        while not self._stop.is_set():
            try:
                text = self.queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                res = self.synth.speak_text_async(text).get()
                if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    play_wav_bytes(res.audio_data, device_index=self.device_index)
                    self.logger.info(f"[{self.name} TTS] bytes={len(res.audio_data)}")
                else:
                    self.logger.error(f"[{self.name} TTS] Problem: {res.reason}")
            except Exception as e:
                self.logger.exception(f"[{self.name} TTS] Error: {e}")
