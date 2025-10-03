# realtime_bridge_dual_cable_streaming.py
# Bridge realtime con streaming parziale + gestione eccezioni robusta

import os, io, wave, time, threading, logging, queue, traceback
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import numpy as np
import sounddevice as sd
import requests
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk

# ====== Config & Log ======
load_dotenv()
def env(name, default=None):
    v = os.getenv(name)
    return v if (v is not None and v.strip()!="") else default

SPEECH_KEY    = env("SPEECH__SERVICE__KEY", "<YOUR_SPEECH_KEY>")
SPEECH_REGION = env("SPEECH__SERVICE__REGION", "westeurope")
TRANSLATOR_KEY      = env("TRANSLATOR_KEY", "<YOUR_TRANSLATOR_KEY>")
TRANSLATOR_REGION   = env("TRANSLATOR_REGION", "westeurope")
TRANSLATOR_ENDPOINT = "https://api.cognitive.microsofttranslator.com"

SRC_LANG      = env("SRC_LANG", "it-IT")
TGT_LANG_UP   = env("TGT_LANG_UP", "en")
DOWN_SRC_LANG = env("DOWN_SRC_LANG", "en-US")
TGT_LANG_DOWN = env("TGT_LANG_DOWN", "it")

CABLE_A_PLAYBACK = env("CABLE_A_PLAYBACK", "CABLE-A Input")
CABLE_B_CAPTURE  = env("CABLE_B_CAPTURE",  "CABLE-B Output")
MY_HEADPHONES    = env("MY_HEADPHONES",    "")
MIC_DEVICE_NAME  = env("MIC_DEVICE_NAME",  "")

SAMPLE_RATE   = 16000
CHANNELS      = 1
FRAME_MS      = 20
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

# VAD params
ENERGY_THRESHOLD = float(env("VAD_THRESHOLD", "0.035"))
HANGOVER_MS      = int(env("VAD_HANGOVER_MS", "300"))
TURN_HYST_MS     = int(env("TURN_HYST_MS", "400"))
MIN_SPEECH_MS    = int(env("MIN_SPEECH_MS", "350"))
SOFT_LOCK_MS     = int(env("SOFT_LOCK_MS", "800"))
BARGE_THRESHOLD  = float(env("BARGE_THRESHOLD", "0.055"))

# Streaming params
ENABLE_PARTIAL_STREAMING = env("ENABLE_PARTIAL_STREAMING", "true").lower() == "true"
PARTIAL_MIN_WORDS = int(env("PARTIAL_MIN_WORDS", "4"))  # Minimo parole per streaming parziale
PARTIAL_DEBOUNCE_MS = int(env("PARTIAL_DEBOUNCE_MS", "800"))  # Debounce tra parziali

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s", 
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bridge")

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
        log.error(f"Errore ricerca device '{keyword}': {e}")
        return None, None

def find_output_device_index(keyword: str): return _find_device_index(keyword, True)
def find_input_device_index(keyword: str):  return _find_device_index(keyword, False)

@dataclass
class Devices:
    mic_idx:    int|None
    a_play_idx: int|None
    b_cap_idx:  int|None
    hp_play_idx:int|None

def resolve_devices() -> Devices:
    try:
        a_idx, a_name = find_output_device_index(CABLE_A_PLAYBACK)
        b_idx, b_name = find_input_device_index(CABLE_B_CAPTURE)
        hp_idx, hp_name = (find_output_device_index(MY_HEADPHONES) if MY_HEADPHONES else (None, None))
        mic_idx, mic_name = (find_input_device_index(MIC_DEVICE_NAME) if MIC_DEVICE_NAME else (None, None))
        
        default_in = default_out = None
        try:
            d = sd.default.device
            if isinstance(d,(list,tuple)) and len(d)==2:
                default_in, default_out = d[0], d[1]
        except Exception:
            pass

        if mic_idx is None:
            if default_in is not None and default_in >= 0:
                log.warning(f"[UP] MIC '{MIC_DEVICE_NAME or '<default>'}' → default IN idx={default_in}")
                mic_idx = default_in
                mic_name = sd.query_devices()[mic_idx]["name"]
            else:
                for i, dev in enumerate(sd.query_devices()):
                    if dev.get("max_input_channels",0) > 0:
                        name = dev.get("name","")
                        if ("cable" in name.lower()) or ("vb-audio" in name.lower()):
                            continue
                        mic_idx = i; mic_name = name
                        log.warning(f"[UP] Fallback MIC: {mic_name} (idx {mic_idx})")
                        break

        if a_idx is None and default_out is not None:
            log.warning(f"[UP] Output '{CABLE_A_PLAYBACK}' → default OUT idx={default_out}")
            a_idx = default_out
            a_name = sd.query_devices()[a_idx]["name"]
        elif a_idx is not None:
            log.info(f"[UP] TTS → {a_name} (idx {a_idx})")

        if b_idx is None and default_in is not None:
            log.warning(f"[DOWN] Input '{CABLE_B_CAPTURE}' → default IN idx={default_in}")
            b_idx = default_in
            b_name = sd.query_devices()[b_idx]["name"]
        elif b_idx is not None:
            log.info(f"[DOWN] Capture ← {b_name} (idx {b_idx})")

        if hp_idx is None and MY_HEADPHONES and default_out is not None:
            log.warning(f"[DOWN] Headphones '{MY_HEADPHONES}' → default OUT idx={default_out}")
            hp_idx = default_out
        elif hp_idx is not None:
            log.info(f"[DOWN] Output → {hp_name} (idx {hp_idx})")

        if mic_idx is not None:
            log.info(f"[UP] MIC: {mic_name} (idx {mic_idx})")

        return Devices(mic_idx=mic_idx, a_play_idx=a_idx, b_cap_idx=b_idx, hp_play_idx=hp_idx)
    except Exception as e:
        log.error(f"Errore risoluzione device: {e}\n{traceback.format_exc()}")
        return Devices(None, None, None, None)

# ====== Utils ======
def wav_bytes_to_float32(wav_bytes: bytes):
    try:
        w = wave.open(io.BytesIO(wav_bytes), 'rb')
        if w.getsampwidth()!=2: 
            raise RuntimeError("TTS atteso PCM 16-bit")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32767.0
        if w.getnchannels()>1:
            data = data.reshape(-1, w.getnchannels())[:,0]
        return data, w.getframerate()
    except Exception as e:
        log.error(f"Errore conversione WAV: {e}")
        raise

def translate_text(text: str, to_lang: str, from_lang: str|None=None) -> Optional[str]:
    if not text or not text.strip(): 
        return None
    try:
        url = f"{TRANSLATOR_ENDPOINT}/translate?api-version=3.0&to={to_lang}"
        if from_lang: url += f"&from={from_lang}"
        h = {
            "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
            "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
            "Content-type": "application/json",
        }
        r = requests.post(url, headers=h, json=[{"text": text}], timeout=10)
        r.raise_for_status()
        return r.json()[0]["translations"][0]["text"]
    except requests.Timeout:
        log.error(f"⏱️ Timeout traduzione: {text[:50]}...")
        return None
    except requests.RequestException as e:
        log.error(f"❌ Errore API traduzione: {e}")
        return None
    except Exception as e:
        log.error(f"❌ Errore traduzione: {e}\n{traceback.format_exc()}")
        return None

# ====== Turn state ======
class Turn(Enum):
    A = "A"
    B = "B"
    IDLE = "IDLE"

class TurnState:
    def __init__(self):
        self.state = Turn.IDLE
        self.lock = threading.Lock()
        self.last_switch_ms = 0.0
        self.speech_start_ms = {"A": 0.0, "B": 0.0}
        self.confidence = {"A": 0.0, "B": 0.0}
        self.active_frames = {"A": 0, "B": 0}
        self.turn_started_at = 0.0
        self.max_energy = {"A": 0.0, "B": 0.0}

    def update_activity(self, channel: str, active: bool, energy: float = 0.0):
        try:
            now_ms = time.time() * 1000
            
            with self.lock:
                other_ch = "B" if channel == "A" else "A"
                
                if active and energy > self.max_energy[channel]:
                    self.max_energy[channel] = energy
                
                if active:
                    self.active_frames[channel] += 1
                    
                    if self.speech_start_ms[channel] == 0.0:
                        self.speech_start_ms[channel] = now_ms
                    
                    duration = now_ms - self.speech_start_ms[channel]
                    frame_factor = min(1.0, self.active_frames[channel] / 8)
                    duration_factor = min(1.0, duration / MIN_SPEECH_MS)
                    energy_factor = min(2.0, energy / ENERGY_THRESHOLD)
                    
                    self.confidence[channel] = frame_factor * duration_factor * energy_factor
                    
                    target = Turn.A if channel == "A" else Turn.B
                    time_in_turn = now_ms - self.turn_started_at
                    
                    # Switch normale
                    if self.state == Turn.IDLE or time_in_turn >= SOFT_LOCK_MS:
                        if (self.confidence[channel] >= 0.75 and 
                            duration >= MIN_SPEECH_MS and
                            self.active_frames[channel] >= 6 and
                            (now_ms - self.last_switch_ms) >= TURN_HYST_MS and
                            self.confidence[other_ch] < 0.4):
                            
                            if self.state != target:
                                old = self.state
                                self.state = target
                                self.last_switch_ms = now_ms
                                self.turn_started_at = now_ms
                                self._reset_channel(other_ch)
                                log.info(f"⚡ TURN {old.value} → {target.value} (conf={self.confidence[channel]:.2f})")
                                return True
                    
                    # Barge-in durante soft-lock
                    elif (self.state != target and 
                          time_in_turn < SOFT_LOCK_MS and
                          energy >= BARGE_THRESHOLD and
                          self.confidence[channel] >= 0.85 and
                          self.active_frames[channel] >= 8 and
                          self.max_energy[channel] >= BARGE_THRESHOLD * 1.2):
                        
                        old = self.state
                        self.state = target
                        self.last_switch_ms = now_ms
                        self.turn_started_at = now_ms
                        self._reset_channel(other_ch)
                        log.warning(f"🎤 BARGE-IN: {channel} interrompe {old.value} (energy={energy:.3f})")
                        return True
                        
                else:
                    self.active_frames[channel] = max(0, self.active_frames[channel] - 2)
                    
                    if self.active_frames[channel] == 0:
                        self.speech_start_ms[channel] = 0.0
                        self.confidence[channel] *= 0.6
                        self.max_energy[channel] *= 0.8
                    
                    if (self.confidence["A"] < 0.05 and 
                        self.confidence["B"] < 0.05 and
                        self.active_frames["A"] == 0 and
                        self.active_frames["B"] == 0):
                        
                        if self.state != Turn.IDLE:
                            self.state = Turn.IDLE
                            self.turn_started_at = now_ms
                            log.info("⏸️ TURN → IDLE")
            
            return False
        except Exception as e:
            log.error(f"Errore update_activity: {e}")
            return False

    def _reset_channel(self, channel: str):
        self.confidence[channel] = 0.0
        self.speech_start_ms[channel] = 0.0
        self.active_frames[channel] = 0
        self.max_energy[channel] = 0.0

    def get_state(self) -> Turn:
        with self.lock:
            return self.state

turn = TurnState()

# ====== VADs ======
class EnergyVAD:
    def __init__(self, threshold=ENERGY_THRESHOLD, hangover_ms=HANGOVER_MS):
        self.th = threshold
        self.hang = hangover_ms
        self.speaking = False
        self.last_voice_ms = 0.0
        self.energy_history = []
        self.max_history = 10
    
    def process_frame(self, pcm16_bytes: bytes, sr=SAMPLE_RATE) -> tuple[bool, bool, float]:
        try:
            x = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)/32767.0
            if len(x)==0: return (False, self.speaking, 0.0)
            
            rms = float(np.sqrt(np.mean(x**2)))
            self.energy_history.append(rms)
            if len(self.energy_history) > self.max_history:
                self.energy_history.pop(0)
            
            avg_energy = np.mean(self.energy_history)
            now = time.time()*1000
            voice = (avg_energy >= self.th)
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
            log.error(f"Errore VAD: {e}")
            return False, self.speaking, 0.0

try:
    import webrtcvad
    _HAVE_WEBRTCVAD = True
except Exception:
    _HAVE_WEBRTCVAD = False

class WebRTCVADWrapper:
    def __init__(self, mode:int=2):
        if not _HAVE_WEBRTCVAD:
            raise RuntimeError("webrtcvad non disponibile")
        self.vad = webrtcvad.Vad(mode)
        self.speaking = False
        self.last_voice_ms = 0.0
        self.hang = HANGOVER_MS
        self.speech_frames = 0
    
    def process_frame(self, pcm16_bytes: bytes, sr=SAMPLE_RATE) -> tuple[bool, bool, float]:
        try:
            is_speech = self.vad.is_speech(pcm16_bytes, sr)
            now = time.time()*1000
            changed = False
            
            x = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)/32767.0
            energy = float(np.sqrt(np.mean(x**2))) if len(x) > 0 else 0.0
            
            if is_speech:
                self.speech_frames += 1
                self.last_voice_ms = now
                if not self.speaking:
                    self.speaking = True
                    changed = True
            else:
                self.speech_frames = 0
                if self.speaking and (now - self.last_voice_ms) > self.hang:
                    self.speaking = False
                    changed = True
            
            return changed, self.speaking, energy
        except Exception as e:
            log.error(f"Errore WebRTC VAD: {e}")
            return False, self.speaking, 0.0

def make_vad():
    if _HAVE_WEBRTCVAD:
        log.info("VAD: WebRTC VAD (mode=2)")
        return WebRTCVADWrapper(mode=2)
    log.info(f"VAD: Energy VAD (threshold={ENERGY_THRESHOLD:.4f})")
    return EnergyVAD()

# ====== Recognizer ======
def make_recognizer(lang: str, push_stream):
    try:
        cfg = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        cfg.speech_recognition_language = lang
        cfg.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "300")
        #cfg.set_property(speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "5000")
        ac = speechsdk.audio.AudioConfig(stream=push_stream)
        return speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=ac)
    except Exception as e:
        log.error(f"Errore creazione recognizer: {e}\n{traceback.format_exc()}")
        raise

# ====== Streaming TTS Engine ======
class StreamingTTS:
    """TTS con gestione seriale e robusta - UN task alla volta"""
    def __init__(self, speech_key, region, out_dev_idx: int|None, label: str):
        try:
            self.synth = speechsdk.SpeechSynthesizer(
                speech_config=speechsdk.SpeechConfig(subscription=speech_key, region=region),
                audio_config=None
            )
        except Exception as e:
            log.error(f"{label} Errore init synthesizer: {e}")
            raise
            
        self.play_idx = out_dev_idx
        self.label = label
        self.speaking_lock = threading.RLock()  # Lock ricorsivo per sicurezza
        self.playing = False
        self.current_stream = None
        self.stop_requested = False
        
        # Gestione parziali
        self.last_partial = ""
        self.last_final = ""
        self.last_speak_time = 0.0
        
    def stop(self):
        """Stop immediato - thread-safe"""
        try:
            self.stop_requested = True
            
            # Stop stream audio
            if self.current_stream:
                try:
                    self.current_stream.abort()
                except Exception:
                    pass
                self.current_stream = None
            
            # Stop synthesizer
            try:
                self.synth.stop_speaking()
            except Exception:
                pass
                
            self.playing = False
            
        except Exception as e:
            log.debug(f"{self.label} Errore stop: {e}")
    
    def speak(self, text: str, is_partial: bool = False):
        """Sintetizza e riproduci - SERIALE, un task alla volta"""
        if not text or not text.strip():
            return
        
        # Lock globale - solo UNA sintesi/riproduzione alla volta
        with self.speaking_lock:
            try:
                # Throttling base
                now = time.time()
                if is_partial and (now - self.last_speak_time) < 0.5:
                    return
                self.last_speak_time = now
                
                # Filtro duplicati
                if is_partial:
                    if text == self.last_partial:
                        return
                    if self.last_final and text.strip() in self.last_final:
                        return
                    self.last_partial = text
                else:
                    if text == self.last_final:
                        return
                    self.last_final = text
                    self.last_partial = ""  # Reset
                
                # Stop qualsiasi riproduzione precedente
                self.stop()
                self.stop_requested = False
                
                log.debug(f"{self.label} Sintesi: '{text[:40]}...' (partial={is_partial})")
                
                # Sintesi - SINCRONA
                self.playing = True
                result = self.synth.speak_text(text)
                
                if self.stop_requested:
                    log.debug(f"{self.label} Interrotto durante sintesi")
                    self.playing = False
                    return
                
                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    # Converti audio
                    audio_data, sample_rate = wav_bytes_to_float32(result.audio_data)
                    
                    if self.stop_requested:
                        self.playing = False
                        return
                    
                    # Riproduci - SINCRONO con chunk interruttibili
                    self._play_audio_sync(audio_data, sample_rate)
                    
                elif result.reason == speechsdk.ResultReason.Canceled:
                    details = result.cancellation_details
                    log.warning(f"{self.label} Cancellato: {details.reason}")
                    if details.error_details:
                        log.error(f"{self.label} Errore: {details.error_details}")
                else:
                    log.warning(f"{self.label} TTS fallito: {result.reason}")
                
                self.playing = False
                
            except Exception as e:
                log.error(f"{self.label} Errore speak: {e}\n{traceback.format_exc()}")
                self.playing = False
    
    def _play_audio_sync(self, audio_data, sample_rate):
        """Riproduzione SINCRONA con controllo interruzione"""
        stream = None
        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                device=self.play_idx,
                dtype='float32',
                blocksize=1024
            )
            stream.start()
            self.current_stream = stream
            
            # Play in chunk da 50ms per permettere interruzione rapida
            chunk_size = int(sample_rate * 0.05)
            
            for i in range(0, len(audio_data), chunk_size):
                if self.stop_requested:
                    log.debug(f"{self.label} Playback interrotto")
                    break
                    
                chunk = audio_data[i:i+chunk_size]
                stream.write(chunk.reshape(-1, 1))
            
            stream.stop()
            
        except Exception as e:
            log.error(f"{self.label} Errore playback: {e}")
        finally:
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass
            self.current_stream = None

# ====== CapturePipe ======
class CapturePipe:
    def __init__(self, in_dev_idx: int, writer, channel: str, label: str, vad=None):
        self.in_dev_idx = in_dev_idx
        self.writer = writer
        self.channel = channel
        self.label = label
        self.buf = b""
        self.vad = vad or make_vad()
        self.stream = None
        self.silence_frames = 0
        self.running = False

    def _cb(self, indata, frames, time_info, status):
        if not self.running:
            return
        
        try:
            if status:
                log.debug(f"{self.label} status: {status}")
            
            pcm16 = (indata[:,0] * 32767.0).astype(np.int16).tobytes()
            self.buf += pcm16
            
            frame_bytes = 2 * FRAME_SAMPLES
            while len(self.buf) >= frame_bytes:
                chunk = self.buf[:frame_bytes]
                self.buf = self.buf[frame_bytes:]
                
                changed, speaking, energy = self.vad.process_frame(chunk, sr=SAMPLE_RATE)
                
                if not speaking:
                    self.silence_frames += 1
                else:
                    self.silence_frames = 0
                
                turn.update_activity(self.channel, speaking, energy)
                self.writer(chunk)
        except Exception as e:
            log.error(f"{self.label} Errore callback: {e}")

    def start(self):
        try:
            if self.in_dev_idx is None:
                raise RuntimeError(f"{self.label}: device input non risolto")
            
            self.running = True
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=FRAME_SAMPLES,
                device=self.in_dev_idx,
                callback=self._cb,
                latency='low'
            )
            self.stream.start()
            log.info(f"{self.label} capture avviata (idx {self.in_dev_idx})")
        except Exception as e:
            log.error(f"{self.label} Errore start: {e}\n{traceback.format_exc()}")
            raise

    def stop(self):
        try:
            self.running = False
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log.error(f"{self.label} Errore stop: {e}")

# ====== Main ======
def main():
    try:
        dev = resolve_devices()
        if dev.a_play_idx is None or dev.b_cap_idx is None or dev.mic_idx is None:
            log.error("❌ Device fondamentali non risolti. Verifica configurazione.")
            return

        # STT streams
        push_up   = speechsdk.audio.PushAudioInputStream()
        rec_up    = make_recognizer(SRC_LANG, push_up)
        push_down = speechsdk.audio.PushAudioInputStream()
        rec_down  = make_recognizer(DOWN_SRC_LANG, push_down)

        # TTS engines (senza worker - chiamate dirette)
        tts_to_meeting = StreamingTTS(SPEECH_KEY, SPEECH_REGION, dev.a_play_idx, "[UP→TTS]")
        tts_to_local   = StreamingTTS(SPEECH_KEY, SPEECH_REGION, dev.hp_play_idx, "[DN→TTS]")
        
        log.info("✅ TTS engines inizializzati")

        # Capture pipes
        cap_up   = CapturePipe(dev.mic_idx,   lambda b: push_up.write(b),   "A", "[UP]")
        cap_down = CapturePipe(dev.b_cap_idx, lambda b: push_down.write(b), "B", "[DOWN]")

        # Recognition handlers con debounce manuale
        last_partial_time = {"up": 0.0, "down": 0.0}
        partial_count = {"up": 0, "down": 0}
        partial_debounce_active = {"up": False, "down": False}
        
        def debounced_partial(text: str, channel: str, tts_engine, to_lang: str, from_lang: str):
            """Gestisce parziale con debounce manuale"""
            try:
                now = time.time()
                
                # Se già in debounce, skip
                if partial_debounce_active[channel]:
                    log.debug(f"[{channel}] Skip parziale (debounce attivo)")
                    return
                
                # Rate limiting
                if now - last_partial_time[channel] < (PARTIAL_DEBOUNCE_MS / 1000.0):
                    log.debug(f"[{channel}] Skip parziale (rate limit)")
                    return
                
                last_partial_time[channel] = now
                partial_debounce_active[channel] = True
                
                def process():
                    try:
                        time.sleep(PARTIAL_DEBOUNCE_MS / 1000.0)
                        partial_count[channel] += 1
                        tr = translate_text(text, to_lang, from_lang)
                        if tr:
                            log.debug(f"[{channel.upper()}~{partial_count[channel]}] {tr[:50]}")
                            tts_engine.speak(tr, is_partial=True)
                    except Exception as e:
                        log.error(f"Errore process partial {channel}: {e}")
                    finally:
                        partial_debounce_active[channel] = False
                
                threading.Thread(target=process, daemon=True).start()
                
            except Exception as e:
                log.error(f"Errore debounced_partial {channel}: {e}")
                partial_debounce_active[channel] = False
        
        def on_recognizing_up(text: str):
            try:
                if not ENABLE_PARTIAL_STREAMING or not text.strip():
                    return
                if turn.get_state() != Turn.A:
                    return
                
                words = text.split()
                if len(words) >= PARTIAL_MIN_WORDS:
                    debounced_partial(text, "up", tts_to_meeting, TGT_LANG_UP, SRC_LANG)
            except Exception as e:
                log.error(f"❌ Errore recognizing UP: {e}")

        def on_final_up(text: str):
            try:
                if not text.strip():
                    return
                if turn.get_state() != Turn.A:
                    log.debug(f"[UP] Ignoro (turno={turn.get_state().value}): {text[:30]}")
                    return
                
                log.info(f"[UP→] {text}")
                partial_count["up"] = 0
                
                tr = translate_text(text, TGT_LANG_UP, SRC_LANG)
                if tr:
                    log.info(f"[→EN] {tr}")
                    tts_to_local.stop()
                    tts_to_meeting.speak(tr, is_partial=False)
                else:
                    log.warning("[UP] Traduzione fallita")
            except Exception as e:
                log.error(f"❌ Errore final UP: {e}\n{traceback.format_exc()}")

        def on_recognizing_down(text: str):
            try:
                if not ENABLE_PARTIAL_STREAMING or not text.strip():
                    return
                if turn.get_state() != Turn.B:
                    return
                
                words = text.split()
                if len(words) >= PARTIAL_MIN_WORDS:
                    debounced_partial(text, "down", tts_to_local, TGT_LANG_DOWN, DOWN_SRC_LANG)
            except Exception as e:
                log.error(f"❌ Errore recognizing DOWN: {e}")

        def on_final_down(text: str):
            try:
                if not text.strip():
                    return
                if turn.get_state() != Turn.B:
                    log.debug(f"[DOWN] Ignoro (turno={turn.get_state().value}): {text[:30]}")
                    return
                
                log.info(f"[DOWN←] {text}")
                partial_count["down"] = 0
                
                tr = translate_text(text, TGT_LANG_DOWN, DOWN_SRC_LANG)
                if tr:
                    log.info(f"[←IT] {tr}")
                    tts_to_meeting.stop()
                    tts_to_local.speak(tr, is_partial=False)
                else:
                    log.warning("[DOWN] Traduzione fallita")
            except Exception as e:
                log.error(f"❌ Errore final DOWN: {e}\n{traceback.format_exc()}")

        # Connect eventi
        try:
            if ENABLE_PARTIAL_STREAMING:
                rec_up.recognizing.connect(lambda e: on_recognizing_up(e.result.text) if e.result.text else None)
                rec_down.recognizing.connect(lambda e: on_recognizing_down(e.result.text) if e.result.text else None)
            
            rec_up.recognized.connect(lambda e: on_final_up(e.result.text) if e.result.text else None)
            rec_down.recognized.connect(lambda e: on_final_down(e.result.text) if e.result.text else None)
            
            # Error handlers
            def on_canceled_up(evt):
                log.error(f"[UP] Recognition canceled: {evt}")
            def on_canceled_down(evt):
                log.error(f"[DOWN] Recognition canceled: {evt}")
            
            rec_up.canceled.connect(on_canceled_up)
            rec_down.canceled.connect(on_canceled_down)
            
        except Exception as e:
            log.error(f"Errore connessione eventi: {e}\n{traceback.format_exc()}")
            raise

        # Run
        cap_up.start()
        cap_down.start()
        rec_up.start_continuous_recognition()
        rec_down.start_continuous_recognition()
        
        log.info("="*70)
        log.info("🎙️  Bridge attivo - Gestione turni bilanciata + Streaming real-time")
        log.info(f"   Min speech: {MIN_SPEECH_MS}ms | Hangover: {HANGOVER_MS}ms")
        log.info(f"   Hysteresis: {TURN_HYST_MS}ms | Soft lock: {SOFT_LOCK_MS}ms")
        log.info(f"   VAD threshold: {ENERGY_THRESHOLD:.3f} | Barge-in: {BARGE_THRESHOLD:.3f}")
        if ENABLE_PARTIAL_STREAMING:
            log.info(f"   🚀 Streaming parziale ATTIVO (min {PARTIAL_MIN_WORDS} parole, debounce {PARTIAL_DEBOUNCE_MS}ms)")
        else:
            log.info("   📝 Streaming parziale DISABILITATO (solo frasi complete)")
        log.info("   💡 Soft lock: primi 800ms resistono a switch, poi barge-in attivo")
        log.info("="*70)
        log.info("Ctrl+C per uscire\n")
        
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        log.info("\n🛑 Stop richiesto")
    except Exception as e:
        log.error(f"❌ Errore fatale: {e}\n{traceback.format_exc()}")
    finally:
        try:
            log.info("Chiusura risorse...")
            
            # Stop recognizers
            try:
                rec_up.stop_continuous_recognition()
            except Exception as e:
                log.debug(f"Errore stop rec_up: {e}")
            
            try:
                rec_down.stop_continuous_recognition()
            except Exception as e:
                log.debug(f"Errore stop rec_down: {e}")
            
            # Close streams
            try:
                push_up.close()
            except Exception as e:
                log.debug(f"Errore close push_up: {e}")
            
            try:
                push_down.close()
            except Exception as e:
                log.debug(f"Errore close push_down: {e}")
            
            # Stop capture
            try:
                cap_up.stop()
            except Exception as e:
                log.debug(f"Errore stop cap_up: {e}")
            
            try:
                cap_down.stop()
            except Exception as e:
                log.debug(f"Errore stop cap_down: {e}")
            
            # Stop TTS
            try:
                tts_to_meeting.stop()
            except Exception as e:
                log.debug(f"Errore stop tts_meeting: {e}")
            
            try:
                tts_to_local.stop()
            except Exception as e:
                log.debug(f"Errore stop tts_local: {e}")
            
            log.info("✅ Terminato")
        except Exception as e:
            log.error(f"Errore durante cleanup: {e}")

if __name__ == "__main__":
    main()