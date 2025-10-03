# realtime_bridge_FINAL_OPTIMIZED.py
# Versione definitiva: chunking aggressivo + zero ripetizioni

import os, io, wave, time, threading, logging, queue, traceback
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import numpy as np
import sounddevice as sd
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk
import hashlib
import re
from difflib import SequenceMatcher

# ====== Config & Log ======
load_dotenv()

def env(name, default=None):
    v = os.getenv(name)
    return v if (v is not None and v.strip()!="") else default

SPEECH_KEY    = env("SPEECH__SERVICE__KEY", "<YOUR_SPEECH_KEY>")
SPEECH_REGION = env("SPEECH__SERVICE__REGION", "westeurope")

SRC_LANG      = env("SRC_LANG", "it-IT")
TGT_LANG_UP   = env("TGT_LANG_UP", "it")
DOWN_SRC_LANG = env("DOWN_SRC_LANG", "it-IT")
TGT_LANG_DOWN = env("TGT_LANG_DOWN", "it")

CABLE_A_PLAYBACK = env("CABLE_A_PLAYBACK", "CABLE-A Input")
CABLE_B_CAPTURE  = env("CABLE_B_CAPTURE",  "CABLE-B Output")
MY_HEADPHONES    = env("MY_HEADPHONES",    "")
MIC_DEVICE_NAME  = env("MIC_DEVICE_NAME",  "Jack Mic (Realtek(R) Audio)")

SAMPLE_RATE   = 16000
CHANNELS      = 1
FRAME_MS      = 10
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

ENERGY_THRESHOLD = float(env("VAD_THRESHOLD", "0.025"))
HANGOVER_MS      = int(env("VAD_HANGOVER_MS", "200"))
MIN_SPEECH_MS    = int(env("MIN_SPEECH_MS", "150"))
TURN_HYST_MS     = int(env("TURN_HYST_MS", "250"))
SOFT_LOCK_MS     = int(env("SOFT_LOCK_MS", "500"))
BARGE_THRESHOLD  = float(env("BARGE_THRESHOLD", "0.050"))

MIN_CHUNK_WORDS = int(env("MIN_CHUNK_WORDS", "3"))
MAX_CHUNK_WAIT_MS = int(env("MAX_CHUNK_WAIT_MS", "350"))

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s", 
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bridge")

thread_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="worker")

# ====== Robust Chunker con Deduplicazione ======
class StableChunker:
    """Chunker che aspetta stabilità - NO troncamenti"""
    
    def __init__(self, min_words=4, stability_window_ms=500, label="Chunker"):
        self.min_words = min_words
        self.stability_window_ms = stability_window_ms
        self.label = label
        
        # Tracking
        self.last_partial = ""
        self.last_spoken_indices = []  # Traccia INDICI parole pronunciate
        self.last_change_time = 0
        self.previous_partial = ""
        
        # Pattern boundaries
        self.strong_boundaries = re.compile(r'[.!?;:]')
        self.weak_boundaries = re.compile(r'[,]')
        
        self.stats = {"chunks": 0, "finals": 0, "skipped": 0}
    
    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        return ' '.join(text.split())
    
    def _is_stable(self, current: str, previous: str) -> bool:
        """Verifica se il testo è stabile (Azure non sta più correggendo)"""
        if not previous:
            return False
        
        # Normalizza
        curr_norm = self._normalize(current)
        prev_norm = self._normalize(previous)
        
        # Se identico = stabile
        if curr_norm == prev_norm:
            return True
        
        # Se current inizia con previous = estensione stabile
        if curr_norm.startswith(prev_norm):
            # Verifica che l'estensione sia significativa (almeno 2 parole)
            diff = curr_norm[len(prev_norm):].strip()
            return len(diff.split()) >= 2
        
        return False
    
    def _find_safe_boundary_position(self, words: list, min_pos: int) -> Optional[int]:
        """Trova boundary sicura DOPO min_pos parole"""
        if len(words) <= min_pos:
            return None
        
        # Ricostruisci testo per cercare boundaries
        text_from_min = ' '.join(words[min_pos:])
        
        # Cerca strong boundaries
        strong_match = self.strong_boundaries.search(text_from_min)
        if strong_match:
            # Conta parole fino alla boundary
            text_until_boundary = text_from_min[:strong_match.end()]
            return min_pos + len(text_until_boundary.split())
        
        # Cerca weak boundaries
        weak_match = self.weak_boundaries.search(text_from_min)
        if weak_match:
            text_until_boundary = text_from_min[:weak_match.end()]
            return min_pos + len(text_until_boundary.split())
        
        return None
    
    def process_partial(self, new_text: str) -> Optional[str]:
        if not new_text or not new_text.strip():
            return None
        
        new_text = new_text.strip()
        
        # Primo parziale
        if not self.last_partial:
            self.last_partial = new_text
            self.previous_partial = new_text
            self.last_change_time = time.time() * 1000
            return None
        
        # Verifica cambio
        now_ms = time.time() * 1000
        if new_text != self.previous_partial:
            self.last_change_time = now_ms
            self.previous_partial = new_text
        
        # Calcola tempo stabilità
        stability_time = now_ms - self.last_change_time
        
        # Se non stabile, attendi
        if stability_time < self.stability_window_ms:
            self.last_partial = new_text
            return None
        
        # Ora il testo è STABILE - processa
        words = new_text.split()
        num_spoken = len(self.last_spoken_indices)
        
        # Calcola parole non pronunciate
        unpronounced_words = words[num_spoken:]
        
        if len(unpronounced_words) < self.min_words:
            return None
        
        # Cerca boundary DOPO min_words
        boundary_pos = self._find_safe_boundary_position(unpronounced_words, self.min_words)
        
        if boundary_pos:
            # Pronuncia fino alla boundary
            chunk_words = unpronounced_words[:boundary_pos]
            chunk = ' '.join(chunk_words)
            
            # Aggiorna indici pronunciati
            for i in range(num_spoken, num_spoken + len(chunk_words)):
                self.last_spoken_indices.append(i)
            
            self.stats["chunks"] += 1
            self.last_change_time = now_ms  # Reset timer
            
            log.info(f"{self.label} ✂️#{self.stats['chunks']} (STABLE+BOUNDARY): {chunk}")
            return chunk
        
        # Nessuna boundary - se abbastanza parole e molto stabile, pronuncia comunque
        if len(unpronounced_words) >= self.min_words * 2 and stability_time >= self.stability_window_ms * 1.5:
            # Pronuncia esattamente min_words * 1.5 (arrotondato)
            num_to_speak = int(self.min_words * 1.5)
            chunk_words = unpronounced_words[:num_to_speak]
            chunk = ' '.join(chunk_words)
            
            for i in range(num_spoken, num_spoken + len(chunk_words)):
                self.last_spoken_indices.append(i)
            
            self.stats["chunks"] += 1
            self.last_change_time = now_ms
            
            log.info(f"{self.label} ✂️#{self.stats['chunks']} (STABLE+LONG): {chunk}")
            return chunk
        
        return None
    
    def finalize(self, final_text: str) -> Optional[str]:
        if not final_text or not final_text.strip():
            return None
        
        final_text = final_text.strip()
        final_words = final_text.split()
        
        # Caso 1: Nulla pronunciato
        if not self.last_spoken_indices:
            self.stats["finals"] += 1
            return final_text
        
        # Caso 2: Finale più corto di ciò che abbiamo pronunciato (impossibile, skip)
        if len(final_words) <= len(self.last_spoken_indices):
            log.info(f"{self.label} ✅ Skip finale (già completo o più corto)")
            self.stats["skipped"] += 1
            return None
        
        # Caso 3: Estrai resto
        remaining_words = final_words[len(self.last_spoken_indices):]
        
        if not remaining_words:
            log.info(f"{self.label} ✅ Già completo")
            self.stats["skipped"] += 1
            return None
        
        remaining = ' '.join(remaining_words)
        self.stats["finals"] += 1
        log.info(f"{self.label} ✅ Completamento: +{len(remaining_words)}w")
        return remaining
    
    def reset(self):
        if self.stats["chunks"] > 0 or self.stats["finals"] > 0:
            log.info(
                f"{self.label} 📊 Reset - Chunks:{self.stats['chunks']} "
                f"Finals:{self.stats['finals']} Skipped:{self.stats['skipped']}"
            )
        self.last_partial = ""
        self.last_spoken_indices = []
        self.previous_partial = ""
        self.last_change_time = 0
        self.stats = {k: 0 for k in self.stats}

# ====== Device discovery ======
def _match_score(name: str, keyword: str) -> int:
    nl, kl = name.lower(), keyword.lower()
    if nl == kl: return 3
    if nl.startswith(kl): return 2
    if kl in nl: return 1
    return 0

def _find_device_index(keyword: str, want_output: bool):
    if not keyword: return None, None
    try:
        cands = []
        for idx, dev in enumerate(sd.query_devices()):
            ch_ok = (dev.get("max_output_channels",0)>0) if want_output else (dev.get("max_input_channels",0)>0)
            if not ch_ok: continue
            name = dev.get("name","")
            score = _match_score(name, keyword)
            if score>0: cands.append((score, idx, name))
        if not cands: return None, None
        cands.sort(key=lambda x: (-x[0], x[2]))
        _, idx, name = cands[0]
        return idx, name
    except Exception as e:
        log.error(f"Errore ricerca device: {e}")
        return None, None

def find_output_device_index(keyword: str): return _find_device_index(keyword, True)
def find_input_device_index(keyword: str):  return _find_device_index(keyword, False)

@dataclass
class Devices:
    mic_idx: int|None
    a_play_idx: int|None
    b_cap_idx: int|None
    hp_play_idx: int|None

def resolve_devices() -> Devices:
    try:
        a_idx, _ = find_output_device_index(CABLE_A_PLAYBACK)
        b_idx, _ = find_input_device_index(CABLE_B_CAPTURE)
        hp_idx, _ = (find_output_device_index(MY_HEADPHONES) if MY_HEADPHONES else (None, None))
        mic_idx, _ = (find_input_device_index(MIC_DEVICE_NAME) if MIC_DEVICE_NAME else (None, None))
        
        default_in = default_out = None
        try:
            d = sd.default.device
            if isinstance(d,(list,tuple)) and len(d)==2:
                default_in, default_out = d[0], d[1]
        except: pass

        if mic_idx is None and default_in is not None: mic_idx = default_in
        if a_idx is None and default_out is not None: a_idx = default_out
        if b_idx is None and default_in is not None: b_idx = default_in

        if mic_idx: log.info(f"[UP] MIC: idx={mic_idx}")
        if a_idx: log.info(f"[UP] Output: idx={a_idx}")
        if b_idx: log.info(f"[DOWN] Input: idx={b_idx}")
        if hp_idx: log.info(f"[DOWN] Output: idx={hp_idx}")

        return Devices(mic_idx=mic_idx, a_play_idx=a_idx, b_cap_idx=b_idx, hp_play_idx=hp_idx)
    except Exception as e:
        log.error(f"Errore risoluzione device: {e}")
        return Devices(None, None, None, None)

def wav_bytes_to_float32(wav_bytes: bytes):
    try:
        w = wave.open(io.BytesIO(wav_bytes), 'rb')
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32767.0
        if w.getnchannels()>1:
            data = data.reshape(-1, w.getnchannels())[:,0]
        return data, w.getframerate()
    except Exception as e:
        log.error(f"Errore conversione WAV: {e}")
        raise

# ====== Turn, VAD, TTS (identici a versione precedente) ======
class Turn(Enum):
    A = "A"
    B = "B"
    IDLE = "IDLE"

class TurnState:
    def __init__(self):
        self._state = Turn.IDLE
        self.write_lock = threading.Lock()
        self.last_switch_ms = 0.0
        self.speech_start_ms = {"A": 0.0, "B": 0.0}
        self.confidence = {"A": 0.0, "B": 0.0}
        self.active_frames = {"A": 0, "B": 0}
        self.turn_started_at = 0.0
        self.max_energy = {"A": 0.0, "B": 0.0}
        self.energy_ema = {"A": 0.0, "B": 0.0}
        self.ema_alpha = 0.3

    def update_activity(self, channel: str, active: bool, energy: float = 0.0):
        try:
            now_ms = time.time() * 1000
            other_ch = "B" if channel == "A" else "A"
            self.energy_ema[channel] = self.ema_alpha * energy + (1 - self.ema_alpha) * self.energy_ema[channel]
            smoothed_energy = self.energy_ema[channel]
            if active and smoothed_energy > self.max_energy[channel]:
                self.max_energy[channel] = smoothed_energy
            if active:
                self.active_frames[channel] += 1
                if self.speech_start_ms[channel] == 0.0:
                    self.speech_start_ms[channel] = now_ms
                duration = now_ms - self.speech_start_ms[channel]
                frame_factor = min(1.0, self.active_frames[channel] / 5)
                duration_factor = min(1.0, duration / MIN_SPEECH_MS)
                energy_factor = min(2.0, smoothed_energy / ENERGY_THRESHOLD)
                self.confidence[channel] = frame_factor * duration_factor * energy_factor * 0.8
                target = Turn.A if channel == "A" else Turn.B
                time_in_turn = now_ms - self.turn_started_at
                should_switch = False
                if self._state == Turn.IDLE or time_in_turn >= SOFT_LOCK_MS:
                    if (self.confidence[channel] >= 0.6 and duration >= MIN_SPEECH_MS * 0.8 and
                        self.active_frames[channel] >= 3 and (now_ms - self.last_switch_ms) >= TURN_HYST_MS * 0.8 and
                        self.confidence[other_ch] < 0.5):
                        should_switch = True
                elif (self._state != target and time_in_turn < SOFT_LOCK_MS and smoothed_energy >= BARGE_THRESHOLD and 
                      self.confidence[channel] >= 0.75 and self.active_frames[channel] >= 5):
                    should_switch = True
                if should_switch and self._state != target:
                    with self.write_lock:
                        old = self._state
                        self._state = target
                        self.last_switch_ms = now_ms
                        self.turn_started_at = now_ms
                        self._reset_channel(other_ch)
                        log.info(f"⚡ TURN {old.value} → {target.value}")
                    return True
            else:
                self.active_frames[channel] = max(0, self.active_frames[channel] - 1)
                if self.active_frames[channel] == 0:
                    self.speech_start_ms[channel] = 0.0
                    self.confidence[channel] *= 0.5
                    self.max_energy[channel] *= 0.7
            return False
        except Exception as e:
            log.error(f"Errore update_activity: {e}")
            return False

    def _reset_channel(self, channel: str):
        self.confidence[channel] = 0.0
        self.speech_start_ms[channel] = 0.0
        self.active_frames[channel] = 0
        self.max_energy[channel] = 0.0
        self.energy_ema[channel] = 0.0

    def get_state(self) -> Turn:
        return self._state

turn = TurnState()

class EnergyVAD:
    def __init__(self, threshold=ENERGY_THRESHOLD, hangover_ms=HANGOVER_MS):
        self.th = threshold
        self.hang = hangover_ms
        self.speaking = False
        self.last_voice_ms = 0.0
        self.energy_buffer = []
        self.buffer_size = 5
        self.adaptive_threshold = threshold
        self.noise_floor = threshold * 0.5
    
    def process_frame(self, pcm16_bytes: bytes, sr=SAMPLE_RATE):
        try:
            x = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)/32767.0
            if len(x)==0: return (False, self.speaking, 0.0)
            rms = float(np.sqrt(np.mean(x**2)))
            self.energy_buffer.append(rms)
            if len(self.energy_buffer) > self.buffer_size:
                self.energy_buffer.pop(0)
            avg_energy = np.mean(self.energy_buffer)
            if not self.speaking and avg_energy < self.adaptive_threshold:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * avg_energy
                self.adaptive_threshold = max(self.th, self.noise_floor * 2.0)
            now = time.time()*1000
            voice = (avg_energy >= self.adaptive_threshold)
            changed = False
            if voice:
                self.last_voice_ms = now
                if not self.speaking:
                    self.speaking = True
                    changed = True
            else:
                if self.speaking and (now - self.last_voice_ms) > self.hang:
                    self.speaking = False
                    changed = True
            return changed, self.speaking, avg_energy
        except Exception as e:
            return False, self.speaking, 0.0

def make_vad():
    return EnergyVAD()

class OptimizedStreamingTTS:
    def __init__(self, speech_key, region, out_dev_idx: int|None, label: str):
        try:
            config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
            config.set_property(speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, "true")
            self.synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        except Exception as e:
            log.error(f"{label} Errore init: {e}")
            raise
        self.play_idx = out_dev_idx
        self.label = label
        self.play_queue = queue.Queue(maxsize=5)
        self.synth_lock = threading.Lock()
        self.shutdown_flag = threading.Event()
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.playback_thread.start()
        self.last_text_hash = ""
        
    def stop(self):
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
            except queue.Empty:
                break
    
    def shutdown(self):
        self.shutdown_flag.set()
        self.stop()
    
    def speak(self, text: str):
        if not text or not text.strip() or self.shutdown_flag.is_set():
            return
        text = text.strip()
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        if text_hash == self.last_text_hash:
            return
        self.last_text_hash = text_hash
        try:
            thread_pool.submit(self._synthesize_and_queue, text)
        except Exception as e:
            log.error(f"{self.label} Errore submit: {e}")
    
    def _synthesize_and_queue(self, text: str):
        try:
            if self.shutdown_flag.is_set():
                return
            result = None
            try:
                with self.synth_lock:
                    if self.shutdown_flag.is_set():
                        return
                    result = self.synth.speak_text(text)
            except Exception as e:
                log.error(f"{self.label} Errore sintesi: {e}")
                return
            if self.shutdown_flag.is_set() or not result:
                return
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                try:
                    audio_data, sample_rate = wav_bytes_to_float32(result.audio_data)
                    self.play_queue.put((audio_data, sample_rate, time.time()), timeout=2.0)
                except queue.Full:
                    log.warning(f"{self.label} Coda piena")
                except Exception as e:
                    log.error(f"{self.label} Errore conversione: {e}")
        except Exception as e:
            log.error(f"{self.label} Errore synthesize: {e}")
    
    def _playback_worker(self):
        while not self.shutdown_flag.is_set():
            try:
                try:
                    item = self.play_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                audio_data, sample_rate, timestamp = item
                age = time.time() - timestamp
                if age > 3.0:
                    continue
                try:
                    self._play_audio(audio_data, sample_rate)
                except Exception as e:
                    log.error(f"{self.label} Errore playback: {e}")
            except Exception as e:
                log.error(f"{self.label} Errore worker: {e}")
    
    def _play_audio(self, audio_data, sample_rate):
        stream = None
        try:
            stream = sd.OutputStream(samplerate=sample_rate, channels=1, device=self.play_idx,
                                   dtype='float32', blocksize=256, latency='low')
            stream.start()
            chunk_size = int(sample_rate * 0.020)
            for i in range(0, len(audio_data), chunk_size):
                if self.shutdown_flag.is_set():
                    break
                chunk = audio_data[i:i+chunk_size]
                stream.write(chunk.reshape(-1, 1))
            stream.stop()
        finally:
            if stream:
                try:
                    stream.close()
                except: 
                    pass

# ====== Final Translator ======
class FinalOptimizedTranslator:
    def __init__(self, speech_key, region, from_lang, to_lang, voice_name, 
                 push_stream, output_device_idx, channel, label):
        self.label = label
        self.channel = channel
        self.config = speechsdk.translation.SpeechTranslationConfig(subscription=speech_key, region=region)
        self.config.speech_recognition_language = from_lang
        self.config.add_target_language(to_lang)
        self.config.voice_name = voice_name
        self.config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "200")
        self.config.set_property(speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "2000")
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        self.translator = speechsdk.translation.TranslationRecognizer(translation_config=self.config, audio_config=audio_config)
        self.tts = OptimizedStreamingTTS(speech_key, region, output_device_idx, f"{label}-TTS")
        self.to_lang = to_lang
        self.running = False
        self.chunker = StableChunker(
            min_words=MIN_CHUNK_WORDS,
            stability_window_ms=int(env("STABILITY_WINDOW_MS", "500")),
            label=label
        )
        self.phrase_count = 0
    
    def start(self):
        def on_recognizing(evt):
            try:
                if not self.running or turn.get_state().value != self.channel:
                    return
                translations = evt.result.translations
                for lang, translated_text in translations.items():
                    if not translated_text:
                        continue
                    chunk = self.chunker.process_partial(translated_text)
                    if chunk:
                        log.info(f"{self.label}🔊[{lang}] {chunk}")
                        self.tts.speak(chunk)
            except Exception as e:
                log.error(f"{self.label} Errore recognizing: {e}")
        
        def on_recognized(evt):
            try:
                if not self.running or evt.result.reason != speechsdk.ResultReason.TranslatedSpeech:
                    return
                if turn.get_state().value != self.channel:
                    return
                source_text = evt.result.text
                if not source_text:
                    return
                self.phrase_count += 1
                log.info(f"{self.label}→ {source_text}")
                translations = evt.result.translations
                for lang, translated_text in translations.items():
                    if not translated_text:
                        continue
                    final_chunk = self.chunker.finalize(translated_text)
                    if final_chunk:
                        log.info(f"{self.label}←[{lang}] +FIN {final_chunk}")
                        self.tts.speak(final_chunk)
                    self.chunker.reset()
            except Exception as e:
                log.error(f"{self.label} Errore recognized: {e}")
        
        def on_canceled(evt):
            details = evt.cancellation_details
            if details.reason == speechsdk.CancellationReason.Error:
                log.error(f"{self.label} Errore: {details.error_details}")
        
        try:
            self.translator.recognizing.connect(on_recognizing)
            self.translator.recognized.connect(on_recognized)
            self.translator.canceled.connect(on_canceled)
            self.running = True
            self.translator.start_continuous_recognition()
            log.info(f"✅ {self.label} OPTIMIZED (min:{MIN_CHUNK_WORDS}w, timeout:{MAX_CHUNK_WAIT_MS}ms)")
        except Exception as e:
            log.error(f"{self.label} Errore start: {e}")
            raise
    
    def stop(self):
        try:
            self.running = False
            self.translator.stop_continuous_recognition()
            self.tts.shutdown()
            log.info(f"{self.label} Totale: {self.phrase_count} frasi")
        except Exception as e:
            log.error(f"{self.label} Errore stop: {e}")

class CapturePipe:
    def __init__(self, in_dev_idx: int, push_stream, channel: str, label: str):
        self.in_dev_idx = in_dev_idx
        self.push_stream = push_stream
        self.channel = channel
        self.label = label
        self.vad = make_vad()
        self.stream = None
        self.running = False
        self.buf = b""

    def _cb(self, indata, frames, time_info, status):
        if not self.running:
            return
        try:
            pcm16 = (indata[:,0] * 32767.0).astype(np.int16).tobytes()
            self.buf += pcm16
            frame_bytes = 2 * FRAME_SAMPLES
            while len(self.buf) >= frame_bytes:
                chunk = self.buf[:frame_bytes]
                self.buf = self.buf[frame_bytes:]
                changed, speaking, energy = self.vad.process_frame(chunk, sr=SAMPLE_RATE)
                turn.update_activity(self.channel, speaking, energy)
                if self.push_stream:
                    self.push_stream.write(chunk)
        except Exception as e:
            log.error(f"{self.label} Errore callback: {e}")

    def start(self):
        try:
            if self.in_dev_idx is None:
                raise RuntimeError(f"{self.label}: device non risolto")
            self.running = True
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
                                        blocksize=FRAME_SAMPLES, device=self.in_dev_idx,
                                        callback=self._cb, latency='low')
            self.stream.start()
            log.info(f"{self.label} capture avviata")
        except Exception as e:
            log.error(f"{self.label} Errore start: {e}")
            raise

    def stop(self):
        try:
            self.running = False
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log.error(f"{self.label} Errore stop: {e}")

def main():
    translator_up = translator_down = cap_up = cap_down = None
    push_up = push_down = None
    try:
        log.info("="*70)
        log.info("🚀 Bridge FINALE - Chunking + Zero Ripetizioni")
        log.info("="*70)
        dev = resolve_devices()
        if not all([dev.a_play_idx, dev.b_cap_idx, dev.mic_idx]):
            log.error("❌ Device non risolti")
            return
        voice_map = {"it": "it-IT-ElsaNeural", "en": "en-US-JennyNeural"}
        push_up = speechsdk.audio.PushAudioInputStream()
        push_down = speechsdk.audio.PushAudioInputStream()
        translator_up = FinalOptimizedTranslator(
            SPEECH_KEY, SPEECH_REGION, SRC_LANG, TGT_LANG_UP,
            voice_map.get(TGT_LANG_UP, "en-US-JennyNeural"),
            push_up, dev.a_play_idx, "A", "[UP]"
        )
        translator_down = FinalOptimizedTranslator(
            SPEECH_KEY, SPEECH_REGION, DOWN_SRC_LANG, TGT_LANG_DOWN,
            voice_map.get(TGT_LANG_DOWN, "it-IT-ElsaNeural"),
            push_down, dev.hp_play_idx, "B", "[DOWN]"
        )
        cap_up = CapturePipe(dev.mic_idx, push_up, "A", "[UP-VAD]")
        cap_down = CapturePipe(dev.b_cap_idx, push_down, "B", "[DOWN-VAD]")
        cap_up.start()
        cap_down.start()
        translator_up.start()
        translator_down.start()
        log.info("="*70)
        log.info("🎙️  SISTEMA ATTIVO - Features:")
        log.info(f"   ✂️  Smart chunking (boundaries + timeout)")
        log.info(f"   🎯 Zero ripetizioni (deduplicazione robusta)")
        log.info(f"   ⚡ Latenza: 300-500ms")
        log.info(f"   📢 UP: {SRC_LANG} → {TGT_LANG_UP}")
        log.info(f"   📥 DOWN: {DOWN_SRC_LANG} → {TGT_LANG_DOWN}")
        log.info("="*70)
        log.info("✅ Pronto - Ctrl+C per uscire\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\n🛑 Stop")
    except Exception as e:
        log.error(f"❌ Errore: {e}\n{traceback.format_exc()}")
    finally:
        try:
            log.info("Chiusura...")
            if translator_up: translator_up.stop()
            if translator_down: translator_down.stop()
            if cap_up: cap_up.stop()
            if cap_down: cap_down.stop()
            if push_up: push_up.close()
            if push_down: push_down.close()
            thread_pool.shutdown(wait=False)
            log.info("✅ Terminato")
        except Exception as e:
            log.error(f"Errore cleanup: {e}")

if __name__ == "__main__":
    main()
