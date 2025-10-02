import threading
from queue import Queue, Empty
import azure.cognitiveservices.speech as speechsdk
from .audio import play_wav_bytes


class TtsWorker:
    def __init__(self, name: str, synthesizer: speechsdk.SpeechSynthesizer, device_index, logger):
        self.name = name
        self.synth = synthesizer
        self.device_index = device_index
        self.logger = logger
        self.queue: Queue[str] = Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1.0)

    def put(self, text: str):
        if text:
            self.queue.put(text)

    def _run(self):
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
                    self.logger.error(f"[{self.name} TTS] Problema: {res.reason}")
            except Exception as e:
                self.logger.exception(f"[{self.name} TTS] Errore: {e}")
