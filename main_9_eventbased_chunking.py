# realtime_bridge_FAST_SPEECH_lowlatency.py
# Pipeline event-based con ottimizzazioni di latenza:
# - OutputStream persistente (no open/close per frase)
# - Scrittura int16 nativa dal chunk Azure (no float)
# - Blocksize ridotto + WASAPI exclusive se disponibile
# - Frame di input a 5 ms verso PushAudioInputStream
# - Parziali client (recognizing) + TTS locale per "parlare mentre parli"

import os, io, wave, time, threading, logging, queue, traceback, re, sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import numpy as np
import sounddevice as sd
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk

# ====== Config & Log ======
load_dotenv()

def env(name, default=None):
    v = os.getenv(name)
    return v if (v is not None and v.strip()!="") else default

SPEECH_KEY    = env("SPEECH__SERVICE__KEY", "<YOUR_SPEECH_KEY>")
SPEECH_REGION = env("SPEECH__SERVICE__REGION", "westeurope")

SRC_LANG      = env("SRC_LANG", "it-IT")
TGT_LANG_UP   = env("TGT_LANG_UP", "da")  # 🇩🇰 Danese
DOWN_SRC_LANG = env("DOWN_SRC_LANG", "da-DK")  # Se l'altra persona parla danese
TGT_LANG_DOWN = env("TGT_LANG_DOWN", "it")  # Tu ricevi in italiano

CABLE_A_PLAYBACK = env("CABLE_A_PLAYBACK", "CABLE-A Input")
CABLE_B_CAPTURE  = env("CABLE_B_CAPTURE",  "CABLE-B Output")
MY_HEADPHONES    = env("MY_HEADPHONES",    "")
MIC_DEVICE_NAME  = env("MIC_DEVICE_NAME",  "")
MIC_DEVICE_INDEX = env("MIC_DEVICE_INDEX")
A_PLAY_INDEX     = env("A_PLAY_INDEX")
B_CAP_INDEX      = env("B_CAP_INDEX")
HP_PLAY_INDEX    = env("HP_PLAY_INDEX")
LIST_DEVICES     = env("LIST_DEVICES", "0") == "1"

# === LATENCY: 5ms frame per push verso Azure (prima era 10ms)
SAMPLE_RATE   = 16000
CHANNELS      = 1
FRAME_MS      = int(env("FRAME_MS", "5"))  # torna a 10 se la macchina non regge
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

ENERGY_THRESHOLD = float(env("VAD_THRESHOLD", "0.025"))
HANGOVER_MS      = int(env("VAD_HANGOVER_MS", "200"))
MIN_SPEECH_MS    = int(env("MIN_SPEECH_MS", "150"))
TURN_HYST_MS     = int(env("TURN_HYST_MS", "250"))
SOFT_LOCK_MS     = int(env("SOFT_LOCK_MS", "500"))
BARGE_THRESHOLD  = float(env("BARGE_THRESHOLD", "0.050"))

# ⚡ PARAMETRI BILANCIATI (chunker)
FIRST_CHUNK_WORDS = int(env("FIRST_CHUNK_WORDS", "2"))
SUBSEQUENT_CHUNK_WORDS = int(env("SUBSEQUENT_CHUNK_WORDS", "5"))
FIRST_CHUNK_STABILITY_MS = int(env("FIRST_CHUNK_STABILITY_MS", "250"))
SUBSEQUENT_STABILITY_MS = int(env("SUBSEQUENT_STABILITY_MS", "500"))

# 🚀 PARAMETRO VELOCITÀ SINTESI (usato per TTS locale dei parziali)
SPEECH_RATE = env("SPEECH_RATE", "+15%")

# Pass-through audio
ENABLE_PASSTHROUGH_UP = env("ENABLE_PASSTHROUGH_UP", "1") == "1"
PASSTHROUGH_VOLUME_UP = float(env("PASSTHROUGH_VOLUME_UP", "0.5"))
ENABLE_PASSTHROUGH_DOWN = env("ENABLE_PASSTHROUGH_DOWN", "0") == "1"
PASSTHROUGH_VOLUME_DOWN = float(env("PASSTHROUGH_VOLUME_DOWN", "0.05"))
TRANSLATION_VOLUME = float(env("TRANSLATION_VOLUME", "1.0"))

# Call center mode
CALL_CENTER_MODE = env("CALL_CENTER_MODE", "1") == "1"
KEYBOARD_SOUND_FILE = env("KEYBOARD_SOUND_FILE", "computer-keyboard-typing-290582.wav")
# Quanto deve durare ogni chunk di tastiera riprodotto (ms) - non cambia FRAME_MS usato per Azure
KEYBOARD_CHUNK_MS = int(env("KEYBOARD_CHUNK_MS", "50"))
KEYBOARD_CHUNK_SAMPLES = int(SAMPLE_RATE * KEYBOARD_CHUNK_MS / 1000)

# Anti-blocking + Anti-troncamento (chunker)
MAX_CHUNK_DELAY_MS = int(env("MAX_CHUNK_DELAY_MS", "3000"))
MAX_PARTIAL_DURATION_MS = int(env("MAX_PARTIAL_DURATION_MS", "4000"))
GROWTH_THRESHOLD_WORDS = int(env("GROWTH_THRESHOLD_WORDS", "15"))
MIN_DELAY_BETWEEN_CHUNKS_MS = int(env("MIN_DELAY_BETWEEN_CHUNKS_MS", "800"))

# >> ATTIVA I PARZIALI <<
USE_PARTIALS = env("USE_PARTIALS", "1")  # "1" = emit parziali via recognizing + TTS locale
FINAL_SEG_MS = env("FINAL_SEG_MS", "100")

# === LATENCY: blocksize più piccolo e WASAPI exclusive opzionale
AUDIO_BLOCKSIZE = int(env("AUDIO_BLOCKSIZE", "128"))   # prova 64 se la scheda lo regge
USE_WASAPI_EXCLUSIVE = env("USE_WASAPI_EXCLUSIVE", "0") == "1"  # solo Windows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bridge")

thread_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="worker")

# ====== BalancedChunker (per parziali) ======
class BalancedChunker:
    """
    Chunker per spezzare parziali in micro-frasi "pronunciabili" evitando troncamenti.
    Evita di emettere troppo spesso e privilegia boundary naturali.
    """
    def __init__(self, label="Chunker"):
        self.label = label
        self.last_spoken_indices = []
        self.last_change_time = 0.0
        self.first_chunk_spoken = False
        self.strong_boundaries = re.compile(r'[.!?;:]')
        self.weak_boundaries = re.compile(r'[,]')
        self.stats = {"chunks": 0, "finals": 0, "skipped": 0}
        self.last_chunk_emission_time = 0.0
        self.partial_start_time = 0.0
        self.max_chunk_delay_ms = MAX_CHUNK_DELAY_MS
        self.max_partial_duration_ms = MAX_PARTIAL_DURATION_MS
        self.growth_threshold_words = GROWTH_THRESHOLD_WORDS
        self.min_delay_between_chunks = MIN_DELAY_BETWEEN_CHUNKS_MS
        self._prev_text = ""

    def _is_word_complete(self, word: str) -> bool:
        if not word: return False
        if len(word) <= 2: return False
        if word[-1] in '.!?,;:': return True
        return len(word) >= 3

    def _find_boundary_after_min(self, words: list, min_words: int) -> Optional[int]:
        if len(words) <= min_words: return None
        search_window = words[min_words:min(len(words), min_words + 4)]
        search_text = ' '.join(search_window)
        m = self.strong_boundaries.search(search_text)
        if m:
            return min_words + len(search_text[:m.end()].split())
        m = self.weak_boundaries.search(search_text)
        if m:
            return min_words + len(search_text[:m.end()].split())
        return None

    def process_partial(self, new_text: str) -> Optional[str]:
        """Ritorna un chunk pronunciabile o None se non conviene ancora emettere."""
        if not new_text or not new_text.strip():
            return None

        now_ms = time.time() * 1000
        if self.partial_start_time == 0.0:
            self.partial_start_time = now_ms
        if self.last_chunk_emission_time == 0.0:
            self.last_chunk_emission_time = now_ms
        if new_text != self._prev_text:
            self.last_change_time = now_ms
            self._prev_text = new_text

        stability_time = now_ms - self.last_change_time
        time_since_last_chunk = now_ms - self.last_chunk_emission_time
        partial_duration = now_ms - self.partial_start_time

        # Hysteresis: non emettere troppo spesso
        if time_since_last_chunk < self.min_delay_between_chunks:
            return None

        if not self.first_chunk_spoken:
            min_words_normal = FIRST_CHUNK_WORDS
            required_stability = FIRST_CHUNK_STABILITY_MS
        else:
            min_words_normal = SUBSEQUENT_CHUNK_WORDS
            required_stability = SUBSEQUENT_STABILITY_MS

        words = new_text.strip().split()
        num_spoken = len(self.last_spoken_indices)
        unspoken = words[num_spoken:]
        n_unspoken = len(unspoken)
        if n_unspoken == 0:
            return None

        # Non emettere se l'ultima parola sembra tronca
        if not self._is_word_complete(unspoken[-1]) and stability_time < required_stability * 1.5:
            return None

        should_emit = False
        if stability_time >= required_stability and n_unspoken >= min_words_normal:
            boundary = self._find_boundary_after_min(unspoken, min_words_normal)
            if boundary or stability_time >= required_stability * 1.5:
                should_emit = True
        elif time_since_last_chunk >= self.max_chunk_delay_ms and n_unspoken >= min_words_normal + 2:
            should_emit = True
        elif n_unspoken >= self.growth_threshold_words:
            should_emit = True
        elif partial_duration >= self.max_partial_duration_ms and n_unspoken >= min_words_normal + 2:
            should_emit = True

        if not should_emit:
            return None

        boundary_pos = self._find_boundary_after_min(unspoken, min_words_normal)
        if boundary_pos:
            chunk_words = unspoken[:boundary_pos]
        else:
            target_words = max(min_words_normal + 2, min(n_unspoken, min_words_normal + 4))
            chunk_words = unspoken[:target_words]

        if chunk_words and not self._is_word_complete(chunk_words[-1]):
            return None

        # Avanza l'indice "pronunciato"
        for i in range(num_spoken, num_spoken + len(chunk_words)):
            self.last_spoken_indices.append(i)

        self.stats["chunks"] += 1
        self.last_chunk_emission_time = now_ms
        self.first_chunk_spoken = True
        return ' '.join(chunk_words)

    def finalize(self, final_text: str) -> Optional[str]:
        if not final_text or not final_text.strip():
            return None
        final_words = final_text.strip().split()
        if not self.last_spoken_indices:
            self.stats["finals"] += 1
            return final_text
        if len(final_words) <= len(self.last_spoken_indices):
            self.stats["skipped"] += 1
            return None
        rem = ' '.join(final_words[len(self.last_spoken_indices):])
        self.stats["finals"] += 1
        return rem

    def reset(self):
        self.last_spoken_indices = []
        self.last_change_time = 0.0
        self.first_chunk_spoken = False
        self.last_chunk_emission_time = 0.0
        self.partial_start_time = 0.0
        self._prev_text = ""
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
        if LIST_DEVICES:
            log.info("📋 Device audio disponibili:")
            for i, dev in enumerate(sd.query_devices()):
                log.info(f"  #{i:02d} {dev.get('name','?')} | IN {dev.get('max_input_channels',0)} | OUT {dev.get('max_output_channels',0)}")
            try:
                log.info(f"Default I/O (sounddevice): {sd.default.device}")
            except:
                pass

        mic_idx = int(MIC_DEVICE_INDEX) if MIC_DEVICE_INDEX and MIC_DEVICE_INDEX.isdigit() else None
        a_idx   = int(A_PLAY_INDEX)     if A_PLAY_INDEX and A_PLAY_INDEX.isdigit()     else None
        b_idx   = int(B_CAP_INDEX)      if B_CAP_INDEX and B_CAP_INDEX.isdigit()       else None
        hp_idx  = int(HP_PLAY_INDEX)    if HP_PLAY_INDEX and HP_PLAY_INDEX.isdigit()   else None

        def pick_name(idx):
            try: return sd.query_devices(idx).get('name','Unknown')
            except: return 'Unknown'

        if mic_idx is None and MIC_DEVICE_NAME:
            _idx, _ = find_input_device_index(MIC_DEVICE_NAME); mic_idx = _idx
        if a_idx is None:
            _idx, _ = find_output_device_index(CABLE_A_PLAYBACK); a_idx = _idx
        if b_idx is None:
            _idx, _ = find_input_device_index(CABLE_B_CAPTURE); b_idx = _idx
        if hp_idx is None and MY_HEADPHONES:
            _idx, _ = find_output_device_index(MY_HEADPHONES); hp_idx = _idx

        try:
            d = sd.default.device
            if isinstance(d,(list,tuple)) and len(d)==2:
                default_in, default_out = d[0], d[1]
                if default_in == -1: default_in = None
                if default_out == -1: default_out = None
            else:
                default_in = default_out = None
        except:
            default_in = default_out = None

        if mic_idx is None and default_in is not None: mic_idx = default_in
        if a_idx   is None and default_out is not None: a_idx   = default_out
        if b_idx   is None and default_in is not None:  b_idx   = default_in

        if mic_idx is None:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels",0) > 0: mic_idx = i; break
        if a_idx is None:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_output_channels",0) > 0: a_idx = i; break
        if b_idx is None:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels",0) > 0: b_idx = i; break
        if hp_idx is None:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_output_channels",0) > 0 and "cable" not in dev.get("name","").lower():
                    hp_idx = i; break

        if mic_idx is not None: log.info(f"✅ [UP] MIC: idx={mic_idx} ({pick_name(mic_idx)})")
        else:                   log.error("❌ [UP] MIC: NOT RESOLVED")
        if a_idx   is not None: log.info(f"✅ [UP] Output: idx={a_idx} ({pick_name(a_idx)})")
        else:                   log.error("❌ [UP] Output: NOT RESOLVED")
        if b_idx   is not None: log.info(f"✅ [DOWN] Input: idx={b_idx} ({pick_name(b_idx)})")
        else:                   log.error("❌ [DOWN] Input: NOT RESOLVED")
        if hp_idx  is not None: log.info(f"✅ [DOWN] Output: idx={hp_idx} ({pick_name(hp_idx)})")
        else:                   log.warning("⚠️  [DOWN] Output: NOT RESOLVED (optional)")

        return Devices(mic_idx=mic_idx, a_play_idx=a_idx, b_cap_idx=b_idx, hp_play_idx=hp_idx)
    except Exception as e:
        log.error(f"Errore risoluzione device: {e}")
        return Devices(None, None, None, None)

def wav_bytes_to_float32(wav_bytes: bytes):
    w = wave.open(io.BytesIO(wav_bytes), 'rb')
    data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32767.0
    if w.getnchannels()>1:
        data = data.reshape(-1, w.getnchannels())[:,0]
    return data, w.getframerate()

# ====== Turn, VAD ======
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
        except Exception:
            return False, self.speaking, 0.0

def make_vad():
    return EnergyVAD()

# ====== Util: volume int16 senza float ======
def scale_int16_bytes(pcm_bytes: bytes, gain: float) -> bytes:
    """Moltiplica int16 per gain usando int32 con clipping → int16, evitando float."""
    if abs(gain - 1.0) < 1e-6:
        return pcm_bytes
    x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.int32)
    x = (x * gain).clip(-32768, 32767).astype(np.int16)
    return x.tobytes()

# ====== Synth condiviso per parziali (TTS locale) ======
_synth_lock = threading.Lock()
_synth = None

def _escape_ssml(text: str) -> str:
    return (text.replace("&","&amp;")
                .replace("<","&lt;")
                .replace(">","&gt;"))

def tts_partial_to_int16_bytes(text: str, voice_name: str, rate: str = SPEECH_RATE) -> bytes:
    """Sintezza un piccolo testo in PCM 16kHz 16bit mono (blocking, ma veloce per micro-chunk)."""
    global _synth
    t = text.strip()
    if not t:
        return b""
    with _synth_lock:
        if _synth is None:
            sc = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
            sc.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
            )
            _synth = speechsdk.SpeechSynthesizer(speech_config=sc, audio_config=None)
        ssml = f"""<speak version='1.0' xml:lang='en-US' xmlns='http://www.w3.org/2001/10/synthesis'>
  <voice name='{voice_name}'>
    <prosody rate='{rate}'>{_escape_ssml(t)}</prosody>
  </voice>
</speak>"""
        res = _synth.speak_ssml(ssml)
        if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return res.audio_data or b""
        return b""
# ==== Anti-click helpers ====
def apply_fade_in_int16(pcm_bytes: bytes, fade_in_ms: int, sr: int = 16000) -> bytes:
    if fade_in_ms <= 0 or not pcm_bytes:
        return pcm_bytes
    x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.int32)
    n_fade = min(len(x), int(sr * fade_in_ms / 1000))
    if n_fade <= 1:
        return pcm_bytes
    # rampa lineare 0→1 su int32, poi clip → int16
    ramp = (np.linspace(0.0, 1.0, n_fade, endpoint=True)).astype(np.float32)
    x[:n_fade] = (x[:n_fade] * ramp).astype(np.int32)
    x = np.clip(x, -32768, 32767).astype(np.int16)
    return x.tobytes()

# ====== Fast Translator con TTS event-based + parziali locali ======
class FastTranslator:
    def __init__(self, speech_key, region, from_lang, to_lang, voice_name, 
                 push_stream, output_device_idx, channel, label, capture_pipe=None):
        self.label = label
        self.channel = channel
        self.capture_pipe = capture_pipe  # 🆕 Riferimento al capture pipe per fermare la tastiera
        ENDPOINT_URL = f"wss://{region}.stt.speech.microsoft.com/speech/universal/v2"
        self.config = speechsdk.translation.SpeechTranslationConfig(endpoint=ENDPOINT_URL, subscription=speech_key)
        self.config.speech_recognition_language = from_lang
        self.config.add_target_language(to_lang)
        self.config.voice_name = voice_name

        # Richiedi boundary e parziali stabili più frequenti
        self.config.set_property(
            speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, "true"
        )
        self.config.set_property(
            speechsdk.PropertyId.SpeechServiceResponse_StablePartialResultThreshold, "1"
        )
        self.config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, FINAL_SEG_MS)
        self.config.set_property(speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "800")

        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        self.translator = speechsdk.translation.TranslationRecognizer(
            translation_config=self.config, audio_config=audio_config
        )
        self._last_write_ms = 0.0
        self._idle_threshold_ms = 600       # se inattivo da >600ms, faccio pre-roll+fade
        self._preroll_ms = 8                # quanti ms di zeri mando prima del chunk
        self._fade_in_ms = 6                # quanto fade-in applicare al chunk
        # OutputStream persistente int16 + low blocksize
        self.play_idx = output_device_idx
        self.audio_stream = None
        self.audio_stream_lock = threading.Lock()
        self.shutdown_flag = threading.Event()

        extra_settings = None
        if sys.platform.startswith("win") and USE_WASAPI_EXCLUSIVE:
            try:
                extra_settings = sd.WasapiSettings(exclusive=True)
                log.info(f"{label} 🎧 WASAPI exclusive attivo")
            except Exception as e:
                log.warning(f"{label} WASAPI exclusive non disponibile: {e}")
                extra_settings = None

        try:
            self.audio_stream = sd.OutputStream(
                samplerate=16000,
                channels=1,
                device=self.play_idx,
                dtype='int16',
                blocksize=AUDIO_BLOCKSIZE,
                latency='low',
                prime_output_buffers_using_stream_callback=True,
                dither_off=True,
                extra_settings=extra_settings
            )
            self.audio_stream.start()
        except Exception as e:
            log.error(f"{label} Errore apertura OutputStream: {e}")
            raise

        self.to_lang = to_lang
        self.running = False
        self.chunker = BalancedChunker(label=label)
        self.phrase_count = 0
        self._voice_name = voice_name

        log.info(f"{label} 🎵 DIRECT synthesizing | Voce: {voice_name} | blocksize={AUDIO_BLOCKSIZE}")
    def _write_pcm_smooth(self, pcm_bytes: bytes):
        
        """Scrive su OutputStream con anti-click: pre-roll di zeri se idle e fade-in sul chunk."""
        if not pcm_bytes:
            return
        now = time.time() * 1000
        was_idle = (now - self._last_write_ms) > self._idle_threshold_ms if self._last_write_ms > 0 else True
        data = pcm_bytes

        if was_idle:
            # pre-roll di silenzio per "aprire" il canale senza click
            preroll_samples = int(16000 * self._preroll_ms / 1000)
            if preroll_samples > 0:
                with self.audio_stream_lock:
                    self.audio_stream.write(np.zeros(preroll_samples, dtype=np.int16))
            # fade-in sul primo chunk reale
            data = apply_fade_in_int16(data, self._fade_in_ms, sr=16000)

        with self.audio_stream_lock:
            self.audio_stream.write(np.frombuffer(data, dtype=np.int16))
        self._last_write_ms = now




    def start(self):
        def on_synthesizing(evt):
            """Audio diretto (dal servizio) per i segmenti finali/robusti."""
            try:
                if self.shutdown_flag.is_set() or turn.get_state().value != self.channel:
                    return
                audio_chunk = evt.result.audio  # bytes int16 @16kHz mono
                if not audio_chunk:
                    return
                
                # 🆕 Ferma la tastiera quando inizia la traduzione (al PRIMO chunk audio)
                if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                    if self.capture_pipe.keyboard_playing:  # Solo se sta suonando
                        self.capture_pipe.keyboard_stop_event.set()
                        log.info(f"{self.label} 🎵 Traduzione avviata → tastiera fermata")
                
                if abs(TRANSLATION_VOLUME - 1.0) > 1e-6:
                    audio_chunk = scale_int16_bytes(audio_chunk, TRANSLATION_VOLUME)
                self._write_pcm_smooth(audio_chunk)
            except Exception as e:
                log.error(f"{self.label} Errore synthesizing: {e}")

        def on_recognizing(evt):
            """Parziali testuali → chunker → TTS locale → stessa OutputStream."""
            if USE_PARTIALS != "1":
                return
            try:
                if self.shutdown_flag.is_set() or turn.get_state().value != self.channel:
                    return
                tr = evt.result.translations.get(self.to_lang, "")
                if not tr:
                    return
                chunk = self.chunker.process_partial(tr)
                if not chunk:
                    return
                pcm = tts_partial_to_int16_bytes(chunk, voice_name=self._voice_name, rate=SPEECH_RATE)
                if pcm and abs(TRANSLATION_VOLUME - 1.0) > 1e-6:
                    pcm = scale_int16_bytes(pcm, TRANSLATION_VOLUME)
                if pcm:
                    self._write_pcm_smooth(pcm)

            except Exception as e:
                log.error(f"{self.label} Errore recognizing: {e}")

        def on_recognized(evt):
            """Fine di un segmento tradotto (il servizio parlerà già via on_synthesizing)."""
            try:
                if not self.running or evt.result.reason != speechsdk.ResultReason.TranslatedSpeech:
                    return
                if turn.get_state().value != self.channel:
                    return
                self.phrase_count += 1
                # reset del chunker per il prossimo periodo
                self.chunker.reset()
            except Exception as e:
                log.error(f"{self.label} Errore recognized: {e}")

        def on_canceled(evt):
            details = evt.cancellation_details
            if details.reason == speechsdk.CancellationReason.Error:
                log.error(f"{self.label} Errore: {details.error_details}")

        try:
            self.translator.synthesizing.connect(on_synthesizing)
            self.translator.recognizing.connect(on_recognizing)  # << parziali
            self.translator.recognized.connect(on_recognized)
            self.translator.canceled.connect(on_canceled)

            self.running = True
            self.translator.start_continuous_recognition()
            log.info(f"✅ {self.label} DIRECT SYNTHESIS avviata (final seg: {FINAL_SEG_MS} ms)")
        except Exception as e:
            log.error(f"{self.label} Errore start: {e}")
            raise

    def stop(self):
        try:
            self.running = False
            self.shutdown_flag.set()
            self.translator.stop_continuous_recognition()
        except Exception as e:
            log.error(f"{self.label} Errore stop recognizer: {e}")
        finally:
            with self.audio_stream_lock:
                if self.audio_stream:
                    try:
                        self.audio_stream.stop()
                        self.audio_stream.close()
                    except Exception as e:
                        log.warning(f"{self.label} Errore chiusura OutputStream: {e}")
                    finally:
                        self.audio_stream = None
            log.info(f"{self.label} Totale frasi: {self.phrase_count}")

# ====== Capture con VAD e pass-through ======
class CapturePipe:
    def __init__(self, in_dev_idx: int, push_stream, channel: str, label: str, 
                 passthrough_device: int|None = None, passthrough_volume: float = 0.5,
                 passthrough_always: bool = False, call_center_mode: bool = False,
                 keyboard_sound_file: str = None):
        self.in_dev_idx = in_dev_idx
        self.push_stream = push_stream
        self.channel = channel
        self.label = label
        self.vad = make_vad()
        self.stream = None
        self.running = False
        self.buf = b""
        self.passthrough_device = passthrough_device
        self.passthrough_volume = passthrough_volume
        self.passthrough_always = passthrough_always
        self.passthrough_buffer = queue.Queue(maxsize=500)  # 🔧 Aumentato da 100 a 500
        self.passthrough_thread = None
        self.passthrough_running = False
        self.call_center_mode = call_center_mode
        self.keyboard_audio = None
        self.keyboard_audio_pos = 0
        self.keyboard_playing = False  # Flag per tracciare se stiamo già riproducendo
        self.last_speech_state = False  # Ultimo stato VAD
        self.keyboard_stop_event = threading.Event()  # 🆕 Evento per fermare la tastiera
        self.other_capture_pipe = None  # 🆕 Riferimento all'altro CapturePipe per fermare la tastiera

        if self.call_center_mode and keyboard_sound_file:
            self._load_keyboard_sound(keyboard_sound_file)

    def _load_keyboard_sound(self, filepath: str):
        """Carica il file audio della tastiera con resampling di alta qualità"""
        try:
            if not os.path.exists(filepath):
                log.warning(f"{self.label} File tastiera non trovato: {filepath}")
                return
            with wave.open(filepath, 'rb') as wav:
                original_rate = wav.getframerate()
                num_frames = wav.getnframes()
                audio_bytes = wav.readframes(num_frames)
                
                # Converti in float32 normalizzato [-1, 1]
                audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                audio_float = audio_int16.astype(np.float32) / 32768.0  # 🔧 Divisione per 32768 (non 32767) per range simmetrico
                
                # 🆕 RESAMPLE ALTA QUALITÀ usando scipy se disponibile
                if original_rate != SAMPLE_RATE:
                    log.info(f"{self.label} 📞 Resampling tastiera: {original_rate}Hz → {SAMPLE_RATE}Hz")
                    try:
                        from scipy import signal
                        import math
                        gcd = math.gcd(original_rate, SAMPLE_RATE)
                        up = SAMPLE_RATE // gcd
                        down = original_rate // gcd
                        log.info(f"{self.label} 📞 Resampling con scipy (up={up}, down={down})")
                        audio_float = signal.resample_poly(audio_float, up, down)
                    except ImportError:
                        log.warning(f"{self.label} scipy non disponibile, uso interpolazione numpy")
                        original_duration = len(audio_float) / original_rate
                        new_length = int(original_duration * SAMPLE_RATE)
                        original_indices = np.linspace(0, len(audio_float) - 1, len(audio_float))
                        new_indices = np.linspace(0, len(audio_float) - 1, new_length)
                        audio_float = np.interp(new_indices, original_indices, audio_float)
                
                # 🔧 NON applicare volume qui - lo faremo durante la riproduzione per preservare qualità
                self.keyboard_audio = audio_float
                
                duration_s = len(self.keyboard_audio) / SAMPLE_RATE
                log.info(f"{self.label} 📞 Caricato suono tastiera: {len(self.keyboard_audio)} samples ({duration_s:.1f}s @ {SAMPLE_RATE}Hz)")
        except Exception as e:
            log.error(f"{self.label} Errore caricamento tastiera: {e}")
            self.keyboard_audio = None

    def _get_keyboard_chunk(self, num_samples: int) -> np.ndarray:
        if self.keyboard_audio is None or len(self.keyboard_audio) == 0:
            return np.zeros(num_samples, dtype=np.float32)
        chunk = np.zeros(num_samples, dtype=np.float32)
        remaining = num_samples
        offset = 0
        while remaining > 0:
            available = len(self.keyboard_audio) - self.keyboard_audio_pos
            to_copy = min(remaining, available)
            chunk[offset:offset+to_copy] = self.keyboard_audio[self.keyboard_audio_pos:self.keyboard_audio_pos+to_copy]
            self.keyboard_audio_pos += to_copy
            if self.keyboard_audio_pos >= len(self.keyboard_audio):
                self.keyboard_audio_pos = 0
            offset += to_copy
            remaining -= to_copy
        return chunk
    
    def _play_keyboard_async(self):
        """🆕 Riproduce l'audio tastiera in chunk, con possibilità di interromperlo"""
        try:
            if self.keyboard_audio is None:
                return
            
            chunk_size = FRAME_SAMPLES  # Usa chunk piccoli per poter interrompere velocemente
            total_samples = len(self.keyboard_audio)
            pos = 0
            
            while pos < total_samples and not self.keyboard_stop_event.is_set():
                end_pos = min(pos + chunk_size, total_samples)
                chunk = self.keyboard_audio[pos:end_pos].astype(np.float32)  # 🔧 Forza float32
                
                # 🔧 Applica volume DURANTE riproduzione (non durante caricamento)
                chunk = chunk * self.passthrough_volume
                
                # 🔧 Anti-clipping: assicurati che il volume non superi [-1, 1]
                chunk = np.clip(chunk, -1.0, 1.0)
                
                try:
                    self.passthrough_buffer.put(chunk, timeout=0.5)  # 🔧 Timeout più lungo
                    pos = end_pos
                except queue.Full:
                    # Buffer pieno, aspetta un po' più a lungo
                    if self.keyboard_stop_event.wait(0.05):
                        break
                    # Se ancora pieno dopo wait, salta questo chunk per non bloccare
                    log.warning(f"{self.label} 📞 Buffer pieno, saltato chunk tastiera")
            
            if self.keyboard_stop_event.is_set():
                log.info(f"{self.label} 📞 Tastiera interrotta")
            
        except Exception as e:
            log.error(f"{self.label} Errore riproduzione tastiera: {e}")

    def _cb(self, indata, frames, time_info, status):
        if not self.running:
            return
        try:
            # indata float32 [-1,1] → int16 bytes per Azure
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

                # 🆕 Se questo canale rileva speech, ferma la tastiera dell'altro canale
                if speaking and self.other_capture_pipe and hasattr(self.other_capture_pipe, 'keyboard_stop_event'):
                    if self.other_capture_pipe.keyboard_playing:
                        self.other_capture_pipe.keyboard_stop_event.set()
                        log.info(f"{self.label} 🛑 Fermata tastiera di {self.other_capture_pipe.label} (speech rilevato)")

                # 🆕 CALL CENTER MODE: Invia TUTTO l'audio tastiera quando inizia a parlare
                if self.call_center_mode and self.keyboard_audio is not None:
                    # Rileva transizione da non-parlante a parlante
                    if speaking and not self.last_speech_state and not self.keyboard_playing:
                        # Inizia a parlare: invia tutto l'audio tastiera in thread separato
                        self.keyboard_stop_event.clear()
                        self.keyboard_playing = True
                        threading.Thread(target=self._play_keyboard_async, daemon=True).start()
                        log.info(f"{self.label} 📞 Avviata riproduzione tastiera")
                    elif not speaking and self.last_speech_state:
                        # Ha finito di parlare: resetta il flag
                        self.keyboard_playing = False
                    
                    self.last_speech_state = speaking
                
                # Pass-through normale (voce originale)
                elif self.passthrough_device is not None and (self.passthrough_always or speaking):
                    try:
                        audio_float = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32767.0
                        audio_float *= self.passthrough_volume
                        self.passthrough_buffer.put_nowait(audio_float)
                    except queue.Full:
                        pass
        except Exception as e:
            log.error(f"{self.label} Errore callback: {e}")

    def _passthrough_worker(self):
        stream = None
        frame_count = 0
        dropped_frames = 0
        last_report_time = time.time()
        try:
            # 🔧 Blocksize più grande per ridurre overhead e migliorare stabilità
            blocksize = FRAME_SAMPLES * 4  # 640 samples invece di 160
            stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, device=self.passthrough_device,
                                     dtype='float32', blocksize=blocksize, latency='low')
            stream.start()
            mode = "CONTINUO" if self.passthrough_always else "VAD"
            if self.call_center_mode and self.keyboard_audio is not None:
                log.info(f"{self.label} 📞 CALL CENTER MODE: Tastiera attiva [{mode}] (vol: {self.passthrough_volume*100:.0f}%)")
            else:
                log.info(f"{self.label} Pass-through attivo [{mode}] (vol: {self.passthrough_volume*100:.0f}%)")
            log.info(f"{self.label} Pass-through blocksize: {blocksize}")
            
            # 🔧 Accumula chunk piccoli in uno più grande per ridurre write operations
            accumulated = []
            accumulated_samples = 0
            
            while self.passthrough_running:
                try:
                    audio = self.passthrough_buffer.get(timeout=0.1)
                    accumulated.append(audio)
                    accumulated_samples += len(audio)
                    
                    # Scrivi quando hai abbastanza samples
                    if accumulated_samples >= blocksize:
                        combined = np.concatenate(accumulated)
                        stream.write(combined.reshape(-1, 1))
                        frame_count += 1
                        accumulated = []
                        accumulated_samples = 0
                    
                    # Report ogni 5 secondi
                    if time.time() - last_report_time > 5.0:
                        queue_size = self.passthrough_buffer.qsize()
                        log.debug(f"{self.label} Pass-through: {frame_count} writes, queue: {queue_size}, dropped: {dropped_frames}")
                        frame_count = 0
                        dropped_frames = 0
                        last_report_time = time.time()
                        
                except queue.Empty:
                    # Scrivi quello che hai accumulato anche se non è completo
                    if accumulated:
                        combined = np.concatenate(accumulated)
                        stream.write(combined.reshape(-1, 1))
                        accumulated = []
                        accumulated_samples = 0
                    continue
                except Exception as e:
                    log.error(f"{self.label} Errore playback pass-through: {e}")
                    dropped_frames += 1
        finally:
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except:
                    pass
            log.info(f"{self.label} Pass-through terminato")

    def start(self):
        try:
            if self.in_dev_idx is None:
                raise RuntimeError(f"{self.label}: device non risolto")
            self.running = True
            if self.passthrough_device is not None:
                self.passthrough_running = True
                self.passthrough_thread = threading.Thread(target=self._passthrough_worker, daemon=True)
                self.passthrough_thread.start()
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
            self.passthrough_running = False
            if self.passthrough_thread:
                self.passthrough_thread.join(timeout=1.0)
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log.error(f"{self.label} Errore stop: {e}")

# ====== Main ======
def main():
    translator_up = translator_down = cap_up = cap_down = None
    push_up = push_down = None
    try:
        log.info("="*70)
        log.info("🚀 Bridge FAST SPEECH - Event-based, low-latency tuned")
        log.info("="*70)
        dev = resolve_devices()

        if any(x is None for x in (dev.a_play_idx, dev.b_cap_idx, dev.mic_idx)):
            log.error("❌ Device non risolti")
            return

        voice_map = {
            "it": "it-IT-GiuseppeNeural",
            "it-IT": "it-IT-GiuseppeNeural",
            "en": "en-US-GuyNeural",
            "en-US": "en-US-GuyNeural",
            "en-GB": "en-GB-RyanNeural"
        }

        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000, bits_per_sample=16, channels=1
        )
        push_up = speechsdk.audio.PushAudioInputStream(audio_format)
        push_down = speechsdk.audio.PushAudioInputStream(audio_format)

        # 🆕 Crea prima i capture pipe
        cap_up = CapturePipe(
            dev.mic_idx, push_up, "A", "[UP-VAD]",
            passthrough_device=dev.a_play_idx if ENABLE_PASSTHROUGH_UP else None,
            passthrough_volume=PASSTHROUGH_VOLUME_UP,
            passthrough_always=False,
            call_center_mode=CALL_CENTER_MODE,
            keyboard_sound_file=KEYBOARD_SOUND_FILE
        )

        # Crea translator con riferimento al capture pipe
        translator_up = FastTranslator(
            SPEECH_KEY, SPEECH_REGION, SRC_LANG, TGT_LANG_UP,
            voice_map.get(TGT_LANG_UP, "en-US-GuyNeural"),
            push_up, dev.a_play_idx, "A", "[UP]", capture_pipe=cap_up
        )

        down_output_device = dev.hp_play_idx
        if down_output_device is None:
            try:
                default_out = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else None
                if default_out is not None:
                    down_output_device = default_out
                    log.info(f"✅ DOWN output: usando default device idx={default_out}")
                else:
                    for i, device in enumerate(sd.query_devices()):
                        if device.get("max_output_channels", 0) > 0:
                            name = device.get("name", "")
                            if "cable" not in name.lower():
                                down_output_device = i
                                log.info(f"✅ DOWN output: auto-selezionato '{name}' (idx={i})")
                                break
            except Exception as e:
                log.error(f"Errore ricerca device DOWN: {e}")

        down_passthrough_device = down_output_device if ENABLE_PASSTHROUGH_DOWN else None
        cap_down = CapturePipe(
            dev.b_cap_idx, push_down, "B", "[DOWN-VAD]",
            passthrough_device=down_passthrough_device,
            passthrough_volume=PASSTHROUGH_VOLUME_DOWN,
            passthrough_always=True
        )

        # 🆕 Collega i due CapturePipe tra loro per fermare la tastiera quando l'altro parla
        cap_up.other_capture_pipe = cap_down
        cap_down.other_capture_pipe = cap_up

        translator_down = FastTranslator(
            SPEECH_KEY, SPEECH_REGION, DOWN_SRC_LANG, TGT_LANG_DOWN,
            voice_map.get(TGT_LANG_DOWN, "it-IT-GiuseppeNeural"),
            push_down, down_output_device, "B", "[DOWN]", capture_pipe=cap_down
        )

        log.info(f"🔧 Pass-through: UP={'ON' if ENABLE_PASSTHROUGH_UP else 'OFF'} @ {PASSTHROUGH_VOLUME_UP*100:.0f}% | DOWN={'ON' if ENABLE_PASSTHROUGH_DOWN else 'OFF'} @ {PASSTHROUGH_VOLUME_DOWN*100:.0f}%")
        log.info(f"🎚️ Traduzione volume: {TRANSLATION_VOLUME*100:.0f}%")
        log.info(f"⏱️ FRAME_MS={FRAME_MS} | AUDIO_BLOCKSIZE={AUDIO_BLOCKSIZE} | WASAPI_EXCL={'ON' if (sys.platform.startswith('win') and USE_WASAPI_EXCLUSIVE) else 'OFF'}")
        log.info(f"📢 UP: {SRC_LANG} → {TGT_LANG_UP}")
        log.info(f"📥 DOWN: {DOWN_SRC_LANG} → {TGT_LANG_DOWN}")
        log.info("="*70)
        log.info("✅ Pronto - Ctrl+C per uscire\n")

        cap_up.start()
        cap_down.start()
        translator_up.start()
        translator_down.start()

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
