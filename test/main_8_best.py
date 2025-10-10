# realtime_bridge_FAST_SPEECH.py
# Versione con sintesi accelerata e chunking migliorato per speech continuo


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



# ====== Config & Log ======
load_dotenv()



def env(name, default=None):
    v = os.getenv(name)
    return v if (v is not None and v.strip()!="") else default



SPEECH_KEY    = env("SPEECH__SERVICE__KEY", "<YOUR_SPEECH_KEY>")
SPEECH_REGION = env("SPEECH__SERVICE__REGION", "westeurope")



SRC_LANG      = env("SRC_LANG", "it-IT")
TGT_LANG_UP   = env("TGT_LANG_UP", "en")  # 🇪🇸 Spagnolo - l'altra persona ti sente in spagnolo
DOWN_SRC_LANG = env("DOWN_SRC_LANG", "en-US")  # 🇪🇸 L'altra persona parla spagnolo
TGT_LANG_DOWN = env("TGT_LANG_DOWN", "it")  # 🇮🇹 Tu senti in italiano



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



# ⚡ PARAMETRI BILANCIATI
FIRST_CHUNK_WORDS = int(env("FIRST_CHUNK_WORDS", "2"))
SUBSEQUENT_CHUNK_WORDS = int(env("SUBSEQUENT_CHUNK_WORDS", "5"))
FIRST_CHUNK_STABILITY_MS = int(env("FIRST_CHUNK_STABILITY_MS", "250"))
SUBSEQUENT_STABILITY_MS = int(env("SUBSEQUENT_STABILITY_MS", "500"))


# 🚀 PARAMETRO VELOCITÀ SINTESI
SPEECH_RATE = env("SPEECH_RATE", "+35%")

# �️ PARAMETRI PASS-THROUGH AUDIO (mix voce originale + traduzione)
# UP: tua voce originale mixata con traduzione inviata al remoto
ENABLE_PASSTHROUGH_UP = env("ENABLE_PASSTHROUGH_UP", "1") == "1"  # Pass-through per direzione UP
PASSTHROUGH_VOLUME_UP = float(env("PASSTHROUGH_VOLUME_UP", "0.05"))  # Volume tua voce originale

# DOWN: voce remota originale mixata con traduzione che senti tu
ENABLE_PASSTHROUGH_DOWN = env("ENABLE_PASSTHROUGH_DOWN", "1") == "1"  # Pass-through per direzione DOWN
PASSTHROUGH_VOLUME_DOWN = float(env("PASSTHROUGH_VOLUME_DOWN", "0.05"))  # Volume voce remota originale

TRANSLATION_VOLUME = float(env("TRANSLATION_VOLUME", "1.0"))  # Volume traduzione (0.0-1.0)

# 📞 MODALITÀ CALL CENTER - Sostituisce voce con rumore tastiera
CALL_CENTER_MODE = env("CALL_CENTER_MODE", "0") == "1"  # Abilita simulazione call center
KEYBOARD_SOUND_FILE = env("KEYBOARD_SOUND_FILE", "computer-keyboard-typing-290582.wav")  # File audio tastiera

# �🆕 PARAMETRI OTTIMIZZATI - Anti-blocking + Anti-troncamento
MAX_CHUNK_DELAY_MS = int(env("MAX_CHUNK_DELAY_MS", "3000"))         # 3 secondi
MAX_PARTIAL_DURATION_MS = int(env("MAX_PARTIAL_DURATION_MS", "4000")) # 4 secondi
GROWTH_THRESHOLD_WORDS = int(env("GROWTH_THRESHOLD_WORDS", "15"))   # 15 parole
MIN_DELAY_BETWEEN_CHUNKS_MS = int(env("MIN_DELAY_BETWEEN_CHUNKS_MS", "800"))

USE_PARTIALS = env("USE_PARTIALS", "0")  # "1" = usa parziali, "0" = solo final
FINAL_SEG_MS = env("FINAL_SEG_MS", "100") 


logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s", 
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bridge")



thread_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="worker")



# ====== 🆕 Improved Balanced Chunker ======
class BalancedChunker:
    """
    Chunker ottimizzato con validazione parole:
    - Evita troncamenti ("fra" vs "frase")
    - Safety net per speech continuo
    - Priorità a boundary naturali
    """
    
    def __init__(self, label="Chunker"):
        self.label = label
        self.last_partial = ""
        self.last_spoken_indices = []
        self.last_change_time = 0
        self.previous_partial = ""
        self.first_chunk_spoken = False
        self.strong_boundaries = re.compile(r'[.!?;:]')
        self.weak_boundaries = re.compile(r'[,]')
        self.stats = {"chunks": 0, "finals": 0, "skipped": 0}
        
        # Parametri ottimizzati
        self.last_chunk_emission_time = 0
        self.partial_start_time = 0
        self.max_chunk_delay_ms = MAX_CHUNK_DELAY_MS
        self.max_partial_duration_ms = MAX_PARTIAL_DURATION_MS
        self.growth_threshold_words = GROWTH_THRESHOLD_WORDS
        self.min_delay_between_chunks = MIN_DELAY_BETWEEN_CHUNKS_MS
    
    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        return ' '.join(text.split())
    
    def _find_boundary_after_min(self, words: list, min_words: int) -> Optional[int]:
        if len(words) <= min_words:
            return None
        search_window = words[min_words:min(len(words), min_words + 4)]
        search_text = ' '.join(search_window)
        strong_match = self.strong_boundaries.search(search_text)
        if strong_match:
            text_until = search_text[:strong_match.end()]
            return min_words + len(text_until.split())
        weak_match = self.weak_boundaries.search(search_text)
        if weak_match:
            text_until = search_text[:weak_match.end()]
            return min_words + len(text_until.split())
        return None
    
    def _is_word_complete(self, word: str) -> bool:
        """🆕 Verifica se una parola sembra completa (non troncata)"""
        if not word:
            return False
        
        # Parole molto corte senza punteggiatura sono sospette
        if len(word) <= 2:
            return False
        
        # Se termina con punteggiatura, è sicuramente completa
        if word[-1] in '.!?,;:':
            return True
        
        # Parole di 3+ caratteri probabilmente complete
        return len(word) >= 3
    
    def process_partial(self, new_text: str) -> Optional[str]:
        if not new_text or not new_text.strip():
            return None
        
        new_text = new_text.strip()
        now_ms = time.time() * 1000
        
        # Inizializzazione
        if not self.last_partial:
            self.last_partial = new_text
            self.previous_partial = new_text
            self.last_change_time = now_ms
            self.partial_start_time = now_ms
            if self.last_chunk_emission_time == 0:
                self.last_chunk_emission_time = now_ms
            return None
        
        # Aggiorna timestamp se il testo è cambiato
        if new_text != self.previous_partial:
            self.last_change_time = now_ms
            self.previous_partial = new_text
        
        # Calcola metriche temporali
        stability_time = now_ms - self.last_change_time
        time_since_last_chunk = now_ms - self.last_chunk_emission_time
        partial_duration = now_ms - self.partial_start_time
        
        # Hysteresis: Non emettere troppo frequentemente
        if time_since_last_chunk < self.min_delay_between_chunks:
            self.last_partial = new_text
            return None
        
        # Determina parametri dinamici con DOPPIO MINIMO
        if not self.first_chunk_spoken:
            min_words_normal = FIRST_CHUNK_WORDS        # 2
            min_words_safety = FIRST_CHUNK_WORDS + 2    # 4 🆕
            required_stability = FIRST_CHUNK_STABILITY_MS
        else:
            min_words_normal = SUBSEQUENT_CHUNK_WORDS   # 5
            min_words_safety = SUBSEQUENT_CHUNK_WORDS + 2  # 7 🆕
            required_stability = SUBSEQUENT_STABILITY_MS
        
        # Analizza parole
        words = new_text.split()
        num_spoken = len(self.last_spoken_indices)
        unpronounced_words = words[num_spoken:]
        unpronounced_count = len(unpronounced_words)
        
        # 🆕 VALIDAZIONE PRE-EMISSIONE: ultima parola deve sembrare completa
        if unpronounced_words and not self._is_word_complete(unpronounced_words[-1]):
            # Se ultima parola sembra troncata, richiedi più stabilità
            if stability_time < required_stability * 1.5:
                self.last_partial = new_text
                return None
        
        # Sistema a 2 livelli con minimo parole differenziato
        should_emit = False
        emission_reason = ""
        
        # LIVELLO 1: OPERAZIONE NORMALE (priorità massima)
        if stability_time >= required_stability and unpronounced_count >= min_words_normal:
            # Preferenza ASSOLUTA per boundary linguistici
            boundary_pos = self._find_boundary_after_min(unpronounced_words, min_words_normal)
            if boundary_pos or stability_time >= required_stability * 1.5:
                should_emit = True
                emission_reason = f"STABLE:{stability_time:.0f}ms"
        
        # LIVELLO 2: SAFETY NET (con minimo parole PIÙ ALTO)
        elif time_since_last_chunk >= self.max_chunk_delay_ms and unpronounced_count >= min_words_safety:
            should_emit = True
            emission_reason = f"TIMEOUT:{time_since_last_chunk:.0f}ms"
        
        elif unpronounced_count >= self.growth_threshold_words:
            should_emit = True
            emission_reason = f"GROWTH:{unpronounced_count}w"
        
        elif partial_duration >= self.max_partial_duration_ms and unpronounced_count >= min_words_safety:
            should_emit = True
            emission_reason = f"FORCED:{partial_duration:.0f}ms"
        
        if not should_emit:
            self.last_partial = new_text
            return None
        
        # Seleziona chunk con PRIORITÀ per boundary naturali
        boundary_pos = self._find_boundary_after_min(unpronounced_words, min_words_normal)
        if boundary_pos:
            chunk_words = unpronounced_words[:boundary_pos]
        else:
            # Nessun boundary: usa minimo safety per evitare frammenti
            target_words = max(min_words_safety, min_words_normal + 3)
            chunk_words = unpronounced_words[:min(unpronounced_count, target_words)]
        
        # 🆕 VALIDAZIONE FINALE: non emettere chunk con parola finale sospetta
        if chunk_words and not self._is_word_complete(chunk_words[-1]):
            self.last_partial = new_text
            return None
        
        chunk = ' '.join(chunk_words)
        
        # Aggiorna indici pronunciati
        for i in range(num_spoken, num_spoken + len(chunk_words)):
            self.last_spoken_indices.append(i)
        
        # Aggiorna statistiche e timestamp
        self.stats["chunks"] += 1
        self.last_chunk_emission_time = now_ms
        self.first_chunk_spoken = True
        self.last_change_time = now_ms
        
        mode = "FIRST" if self.stats["chunks"] == 1 else "FLOW"
        log.info(f"{self.label} ✂️#{self.stats['chunks']} [{mode}|{emission_reason}|{len(chunk_words)}w]: {chunk}")
        
        return chunk
    
    def finalize(self, final_text: str) -> Optional[str]:
        if not final_text or not final_text.strip():
            return None
        
        final_text = final_text.strip()
        final_words = final_text.split()
        
        if not self.last_spoken_indices:
            self.stats["finals"] += 1
            return final_text
        
        if len(final_words) <= len(self.last_spoken_indices):
            log.info(f"{self.label} ✅ Skip finale (completo)")
            self.stats["skipped"] += 1
            return None
        
        remaining_words = final_words[len(self.last_spoken_indices):]
        
        if not remaining_words:
            log.info(f"{self.label} ✅ Completo")
            self.stats["skipped"] += 1
            return None
        
        remaining = ' '.join(remaining_words)
        self.stats["finals"] += 1
        log.info(f"{self.label} ✅ Fine: +{len(remaining_words)}w")
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
        self.first_chunk_spoken = False
        self.partial_start_time = 0
        self.stats = {k: 0 for k in self.stats}

    
    def finalize(self, final_text: str) -> Optional[str]:
        if not final_text or not final_text.strip():
            return None
        
        final_text = final_text.strip()
        final_words = final_text.split()
        
        if not self.last_spoken_indices:
            self.stats["finals"] += 1
            return final_text
        
        if len(final_words) <= len(self.last_spoken_indices):
            log.info(f"{self.label} ✅ Skip finale (completo)")
            self.stats["skipped"] += 1
            return None
        
        remaining_words = final_words[len(self.last_spoken_indices):]
        
        if not remaining_words:
            log.info(f"{self.label} ✅ Completo")
            self.stats["skipped"] += 1
            return None
        
        remaining = ' '.join(remaining_words)
        self.stats["finals"] += 1
        log.info(f"{self.label} ✅ Fine: +{len(remaining_words)}w")
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
        self.first_chunk_spoken = False
        self.partial_start_time = 0  # 🆕 Reset anche questo
        # NON resettiamo last_chunk_emission_time per mantenere continuità tra frasi
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
        a_idx, a_name = find_output_device_index(CABLE_A_PLAYBACK)
        b_idx, b_name = find_input_device_index(CABLE_B_CAPTURE)
        hp_idx, hp_name = (find_output_device_index(MY_HEADPHONES) if MY_HEADPHONES else (None, None))
        mic_idx, mic_name = (find_input_device_index(MIC_DEVICE_NAME) if MIC_DEVICE_NAME else (None, None))
        
        # Log initial search results
        if mic_idx:
            log.info(f"[UP] MIC found: '{mic_name}' (idx={mic_idx})")
        else:
            log.warning(f"[UP] MIC not found for keyword: '{MIC_DEVICE_NAME}'")
        
        default_in = default_out = None
        try:
            d = sd.default.device
            if isinstance(d,(list,tuple)) and len(d)==2:
                default_in, default_out = d[0], d[1]
                log.info(f"System defaults: input={default_in}, output={default_out}")
        except Exception as e:
            log.warning(f"Could not get default devices: {e}")
        
        # Apply fallbacks
        if mic_idx is None and default_in is not None: 
            mic_idx = default_in
            try:
                mic_name = sd.query_devices(default_in).get('name', 'Unknown')
                log.info(f"[UP] MIC using default input: '{mic_name}' (idx={mic_idx})")
            except:
                pass
        
        # If still no mic, find first available input device (excluding virtual cables)
        if mic_idx is None:
            log.warning("[UP] Searching for first available physical input device...")
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    name = dev.get("name", "")
                    # Skip virtual cables, prefer physical mics
                    if any(keyword in name.lower() for keyword in ["cable", "virtual", "loopback", "mapper"]):
                        continue
                    mic_idx = i
                    mic_name = name
                    log.info(f"[UP] MIC auto-selected: '{mic_name}' (idx={mic_idx})")
                    break
            
            # If still nothing, use ANY input device as last resort
            if mic_idx is None:
                for i, dev in enumerate(sd.query_devices()):
                    if dev.get("max_input_channels", 0) > 0:
                        mic_idx = i
                        mic_name = dev.get("name", "Unknown")
                        log.warning(f"[UP] MIC fallback to: '{mic_name}' (idx={mic_idx})")
                        break
        
        if a_idx is None and default_out is not None: a_idx = default_out
        if b_idx is None and default_in is not None: b_idx = default_in
        
        # Final status
        if mic_idx: 
            log.info(f"✅ [UP] MIC: idx={mic_idx}")
        else:
            log.error(f"❌ [UP] MIC: NOT RESOLVED")
        
        if a_idx: 
            log.info(f"✅ [UP] Output: idx={a_idx}")
        else:
            log.error(f"❌ [UP] Output: NOT RESOLVED")
            
        if b_idx: 
            log.info(f"✅ [DOWN] Input: idx={b_idx}")
        else:
            log.error(f"❌ [DOWN] Input: NOT RESOLVED")
            
        if hp_idx: 
            log.info(f"✅ [DOWN] Output: idx={hp_idx}")
        else:
            log.warning(f"⚠️  [DOWN] Output: NOT RESOLVED (optional)")
        
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
        except Exception as e:
            return False, self.speaking, 0.0



def make_vad():
    return EnergyVAD()



# ====== Fast Streaming TTS ======
class FastStreamingTTS:
    """TTS con velocità configurabile tramite SSML"""
    
    def __init__(self, speech_key, region, voice_name: str, out_dev_idx: int|None, label: str, speech_rate: str = "+25%", on_playback_start=None):
        try:
            config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
            config.set_property(speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, "true")
            #config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "100")
            config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
            )
            self.synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        except Exception as e:
            log.error(f"{label} Errore init: {e}")
            raise
        self.play_idx = out_dev_idx
        self.label = label
        self.speech_rate = speech_rate
        self.voice_name = voice_name
        self.play_queue = queue.Queue(maxsize=5)
        self.synth_lock = threading.Lock()
        self.shutdown_flag = threading.Event()
        self.on_playback_start = on_playback_start  # 🆕 Callback quando inizia la riproduzione
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.playback_thread.start()
        self.last_text_hash = ""
        
        log.info(f"{label} Velocità: {speech_rate}, Voce: {voice_name}")
        
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
            
            ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='it-IT'>
                <voice name='{self.voice_name}'>
                    <prosody rate='{self.speech_rate}'>
                        {text}
                    </prosody>
                </voice>
            </speak>"""
            
            result = None
            try:
                with self.synth_lock:
                    if self.shutdown_flag.is_set():
                        return
                    result = self.synth.speak_ssml(ssml)
            except Exception as e:
                log.error(f"{self.label} Errore sintesi: {e}")
                return
            
            if self.shutdown_flag.is_set() or not result:
                return
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                try:
                    audio_data, sample_rate = wav_bytes_to_float32(result.audio_data)
                    # Applica volume traduzione
                    audio_data = audio_data * TRANSLATION_VOLUME
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
            # 🆕 Chiama callback PRIMA di iniziare la riproduzione
            if self.on_playback_start:
                try:
                    self.on_playback_start()
                except Exception as e:
                    log.error(f"{self.label} Errore callback playback_start: {e}")
            
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



# ====== Fast Translator ======
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
        self.config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, FINAL_SEG_MS)
        self.config.set_property(speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "1500")
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        self.translator = speechsdk.translation.TranslationRecognizer(translation_config=self.config, audio_config=audio_config)
        
        # 🆕 Crea TTS con callback per fermare la tastiera quando inizia la riproduzione
        self.tts = FastStreamingTTS(
            speech_key, region, voice_name, output_device_idx, f"{label}-TTS", SPEECH_RATE,
            on_playback_start=self._on_translation_start
        )
        
        self.to_lang = to_lang
        self.running = False
        self.chunker = BalancedChunker(label=label)
        self.phrase_count = 0
    
    def _on_translation_start(self):
        """Chiamato quando il TTS inizia a riprodurre la traduzione"""
        if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
            if self.capture_pipe.keyboard_playing:  # Solo se sta suonando
                self.capture_pipe.keyboard_stop_event.set()
                log.info(f"{self.label} 🎵 TTS avviato → tastiera fermata")
    
    def start(self):
        def on_recognizing(evt):
            if USE_PARTIALS != "1":
                return
            try:
                if not self.running or turn.get_state().value != self.channel:
                    return
                
                # 🆕 Ferma la tastiera quando inizia la traduzione
                if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                    self.capture_pipe.keyboard_stop_event.set()
                
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

                src_text = evt.result.text or ""
                if not src_text:
                    return

                self.phrase_count += 1
                log.info(f"{self.label}→ {src_text}")

                # 🆕 Ferma la tastiera quando arriva la traduzione finale
                if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                    self.capture_pipe.keyboard_stop_event.set()

                # 🟢 Solo FINALI: parla l'intera traduzione del final
                for lang, translated_text in evt.result.translations.items():
                    if not translated_text:
                        continue
                    log.info(f"{self.label}←[{lang}] FINAL {translated_text}")
                    self.tts.speak(translated_text)

                # pulizia chunker (non strettamente necessario ora)
                self.chunker.reset()

            except Exception as e:
                log.error(f"{self.label} Errore recognized: {e}")

        def on_canceled(evt):
            details = evt.cancellation_details
            if details.reason == speechsdk.CancellationReason.Error:
                log.error(f"{self.label} Errore: {details.error_details}")
        
        try:
            # Collega recognizing SOLO se vuoi i parziali
            if USE_PARTIALS == "1":
                self.translator.recognizing.connect(on_recognizing)

            self.translator.recognized.connect(on_recognized)
            self.translator.canceled.connect(on_canceled)

            self.running = True
            self.translator.start_continuous_recognition()

            mode = "FINALS_ONLY" if USE_PARTIALS != "1" else "PARTIALS+FINALS"
            log.info(f"✅ {self.label} FAST [{mode}] (final seg: {FINAL_SEG_MS} ms, speed:{SPEECH_RATE})")

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
        self.passthrough_always = passthrough_always  # True = sempre, False = solo quando speaking
        self.passthrough_buffer = queue.Queue(maxsize=500)  # 🔧 Aumentato da 100 a 500
        self.passthrough_thread = None
        self.passthrough_running = False
        self.call_center_mode = call_center_mode
        self.keyboard_audio = None
        self.keyboard_audio_pos = 0
        self.keyboard_playing = False  # 🆕 Flag per tracciare se stiamo riproducendo
        self.last_speech_state = False  # 🆕 Ultimo stato VAD
        self.keyboard_stop_event = threading.Event()  # 🆕 Evento per fermare la tastiera
        self.other_capture_pipe = None  # 🆕 Riferimento all'altro CapturePipe per fermare la tastiera
        
        # Carica audio tastiera se in modalità call center
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
                        # Calcola il GCD per trovare up/down ottimali
                        import math
                        gcd = math.gcd(original_rate, SAMPLE_RATE)
                        up = SAMPLE_RATE // gcd
                        down = original_rate // gcd
                        log.info(f"{self.label} 📞 Resampling con scipy (up={up}, down={down})")
                        audio_float = signal.resample_poly(audio_float, up, down)
                    except ImportError:
                        # Fallback a numpy interpolazione (meno accurato ma funziona)
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
        """Ottiene un chunk del suono tastiera in loop"""
        if self.keyboard_audio is None or len(self.keyboard_audio) == 0:
            # Fallback: silenzio
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
                self.keyboard_audio_pos = 0  # Loop
            
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

            
            if self.keyboard_stop_event.is_set():
                log.info(f"{self.label} 📞 Tastiera interrotta")
            
        except Exception as e:
            log.error(f"{self.label} Errore riproduzione tastiera: {e}")


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
    
    def _get_keyboard_chunk(self, num_samples: int) -> np.ndarray:
        """Ottiene un chunk del suono tastiera in loop"""
        if self.keyboard_audio is None or len(self.keyboard_audio) == 0:
            # Fallback: silenzio
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
                self.keyboard_audio_pos = 0  # Loop
            
            offset += to_copy
            remaining -= to_copy
        
        return chunk


    def _passthrough_worker(self):
        """Thread per riprodurre l'audio originale con volume ridotto"""
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
                log.info(f"{self.label} 📞 CALL CENTER MODE: Tastiera attiva [{mode}] (volume: {self.passthrough_volume*100:.0f}%)")
            else:
                log.info(f"{self.label} Pass-through attivo [{mode}] (volume: {self.passthrough_volume*100:.0f}%)")
            log.info(f"{self.label} Pass-through device idx: {self.passthrough_device}, blocksize: {blocksize}")
            
            # 🔧 Accumula chunk piccoli in uno più grande per ridurre write operations
            accumulated = []
            accumulated_samples = 0
            
            while self.passthrough_running:
                try:
                    audio = self.passthrough_buffer.get(timeout=0.1)
                    accumulated.append(audio)
                    accumulated_samples += len(audio)
                    
                    # Scrivi quando hai abbastanza samples o dopo timeout
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
            log.info(f"{self.label} Pass-through terminato (totale frame: {frame_count})")
    
    def start(self):
        try:
            if self.in_dev_idx is None:
                raise RuntimeError(f"{self.label}: device non risolto")
            self.running = True
            
            # Avvia thread pass-through se abilitato (il controllo ENABLE_PASSTHROUGH_UP/DOWN è fatto nel main)
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



def main():
    translator_up = translator_down = cap_up = cap_down = None
    push_up = push_down = None
    try:
        log.info("="*70)
        log.info("🚀 Bridge FAST SPEECH - Anti-Blocking Continuo")
        log.info("="*70)
        dev = resolve_devices()
        if not all([dev.a_play_idx, dev.b_cap_idx, dev.mic_idx]):
            log.error("❌ Device non risolti")
            return

        voice_map = {
            "it": "it-IT-GiuseppeNeural",
            "it-IT": "it-IT-GiuseppeNeural",
            "en": "en-US-GuyNeural",
            "en-US": "en-US-GuyNeural",
            "es": "es-ES-ElviraNeural",
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
            passthrough_always=False,  # Solo quando parli
            call_center_mode=CALL_CENTER_MODE,
            keyboard_sound_file=KEYBOARD_SOUND_FILE
        )
        
        # Crea translator con riferimento al capture pipe
        translator_up = FastTranslator(
            SPEECH_KEY, SPEECH_REGION, SRC_LANG, TGT_LANG_UP,
            voice_map.get(TGT_LANG_UP, "en-US-GuyNeural"),
            push_up, dev.a_play_idx, "A", "[UP]", capture_pipe=cap_up
        )
        
        # Se le cuffie non sono specificate, usa il device di output di default per DOWN
        down_output_device = dev.hp_play_idx
        if down_output_device is None:
            log.warning("⚠️  Cuffie non configurate, cerco device di default per DOWN")
            try:
                default_out = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else None
                if default_out is not None:
                    down_output_device = default_out
                    log.info(f"✅ DOWN output: usando default device idx={default_out}")
                else:
                    # Cerca il primo device di output disponibile
                    for i, device in enumerate(sd.query_devices()):
                        if device.get("max_output_channels", 0) > 0:
                            # Skip virtual cables per preferire dispositivi fisici
                            name = device.get("name", "")
                            if "cable" not in name.lower():
                                down_output_device = i
                                log.info(f"✅ DOWN output: auto-selezionato '{name}' (idx={i})")
                                break
            except Exception as e:
                log.error(f"Errore ricerca device DOWN: {e}")
        
        # DOWN: voce remota originale (continua) + traduzione -> stesso device della traduzione
        down_passthrough_device = down_output_device if ENABLE_PASSTHROUGH_DOWN else None
        cap_down = CapturePipe(
            dev.b_cap_idx, push_down, "B", "[DOWN-VAD]",
            passthrough_device=down_passthrough_device,
            passthrough_volume=PASSTHROUGH_VOLUME_DOWN,
            passthrough_always=True  # Sempre, per sentire tutto l'audio remoto
        )
        
        # 🆕 Collega i due CapturePipe tra loro per fermare la tastiera quando l'altro parla
        cap_up.other_capture_pipe = cap_down
        cap_down.other_capture_pipe = cap_up
        
        translator_down = FastTranslator(
            SPEECH_KEY, SPEECH_REGION, DOWN_SRC_LANG, TGT_LANG_DOWN,
            voice_map.get(TGT_LANG_DOWN, "it-IT-GiuseppeNeural"),
            push_down, down_output_device, "B", "[DOWN]", capture_pipe=cap_down
        )
        
        # Debug info
        log.info(f"🔧 DEBUG - Configurazione pass-through:")
        log.info(f"   UP: passthrough_device={dev.a_play_idx if ENABLE_PASSTHROUGH_UP else None}, volume={PASSTHROUGH_VOLUME_UP*100:.0f}%, mode=VAD")
        log.info(f"   DOWN: passthrough_device={down_passthrough_device}, volume={PASSTHROUGH_VOLUME_DOWN*100:.0f}%, mode=CONTINUO")
        if down_passthrough_device:
            try:
                dev_info = sd.query_devices(down_passthrough_device)
                log.info(f"   DOWN device name: {dev_info.get('name', 'Unknown')}")
            except:
                pass
        
        cap_up.start()
        cap_down.start()
        translator_up.start()
        translator_down.start()
        log.info("="*70)
        log.info("🎙️  SISTEMA ATTIVO - Modalità Anti-Blocking:")
        log.info(f"   🚀 Velocità sintesi: {SPEECH_RATE}")
        if CALL_CENTER_MODE:
            log.info(f"   📞 CALL CENTER MODE: ATTIVO (suono tastiera)")
            log.info(f"       └─ File: {KEYBOARD_SOUND_FILE}")
        log.info(f"   🎚️  Pass-through UP (tua voce): {'ATTIVO' if ENABLE_PASSTHROUGH_UP else 'DISATTIVO'}")
        if ENABLE_PASSTHROUGH_UP:
            audio_type = "Tastiera" if CALL_CENTER_MODE else "Voce originale"
            log.info(f"       └─ {audio_type}: {PASSTHROUGH_VOLUME_UP*100:.0f}% | Traduzione: {TRANSLATION_VOLUME*100:.0f}%")
        log.info(f"   🎚️  Pass-through DOWN (voce remota): {'ATTIVO' if ENABLE_PASSTHROUGH_DOWN else 'DISATTIVO'}")
        if ENABLE_PASSTHROUGH_DOWN:
            log.info(f"       └─ Volume originale: {PASSTHROUGH_VOLUME_DOWN*100:.0f}% | Traduzione: {TRANSLATION_VOLUME*100:.0f}%")
        log.info(f"   ⏱️  Timeout chunk: {MAX_CHUNK_DELAY_MS}ms")
        log.info(f"   📏 Crescita forzata: {GROWTH_THRESHOLD_WORDS} parole")
        log.info(f"   ⏳ Max partial: {MAX_PARTIAL_DURATION_MS}ms")
        log.info(f"   ⚡ Primo chunk: {FIRST_CHUNK_WORDS}w @ {FIRST_CHUNK_STABILITY_MS}ms")
        log.info(f"   🌊 Successivi: {SUBSEQUENT_CHUNK_WORDS}w @ rilassata 100ms")
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