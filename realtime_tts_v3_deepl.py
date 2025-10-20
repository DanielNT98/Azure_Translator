import os
import sys
import time
import json
import base64
import queue
import threading
import logging
import asyncio
from pathlib import Path
from urllib.parse import urlencode
import contextlib
from typing import Callable
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import numpy as np
import sounddevice as sd
try:
    from websockets.asyncio.client import ClientConnection, connect
except ImportError:
    from websockets.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
import aiohttp
from dotenv import load_dotenv

# === RealtimeTTS ===
from RealtimeTTS import (
    TextToAudioStream,
    AzureEngine,
    SystemEngine,
    ElevenlabsEngine,
    OpenAIEngine,
)

# === Load environment ===
env_path = Path(__file__).with_name(".env_v2")
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=False)
else:
    load_dotenv(override=False)

# === Azure Speech for TTS ===
AZURE_SPEECH_KEY    = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "westeurope")
AZURE_TTS_RATE      = os.getenv("AZURE_TTS_RATE", "25")
AZURE_TTS_VOICE    = os.getenv("AZURE_TTS_VOICE")

# === DeepL Voice Realtime ===
DEEPL_VOICE_API_KEY = os.getenv("DEEPL_VOICE_API_KEY")
DEEPL_VOICE_API_URL = os.getenv("DEEPL_VOICE_API_URL", "https://api.deepl.com/v1/voice/realtime")
SOURCE_LANGUAGE     = os.getenv("SRC_LANG", "it")
TARGET_LANGUAGE     = os.getenv("DST_LANG", "en")

# === Audio capture config (microphone) ===
SAMPLE_RATE   = 16000
CHANNELS      = 1
DTYPE         = "int16"
CHUNK_MS      = 60
FRAMES_PER_CHUNK = int(SAMPLE_RATE * CHUNK_MS / 1000)
MEDIA_CONTENT_TYPE = f"audio/pcm;encoding=s16le;rate={SAMPLE_RATE}"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE = os.getenv("ELEVENLABS_VOICE", "Brian")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# Map ElevenLabs voice IDs per language to keep characters consistent.
ELEVENLABS_VOICE_ID_BY_LANG = {
    "en": "6YQMyaUWlj0VX652cY1C",
    "it": "qxjGnozOAtD4eqNuXms4",
}
_elevenlabs_voice_override = os.getenv("ELEVENLABS_VOICE_ID")
ELEVENLABS_VOICE_ID = _elevenlabs_voice_override or ELEVENLABS_VOICE_ID_BY_LANG.get(
    (TARGET_LANGUAGE or "").lower(),
    "6YQMyaUWlj0VX652cY1C",
)

OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "tts-1")

_TTS_ENGINE_ENV = os.getenv("TTS_ENGINE", "elevenlabs")
if _TTS_ENGINE_ENV:
    TTS_ENGINE_NAME = _TTS_ENGINE_ENV.strip().lower()
else:
    TTS_ENGINE_NAME = "elevenlabs"

AZURE_DEFAULT_VOICE_BY_LANG = {
    "en": "en-US-AshleyNeural",
    "it": "it-IT-FabiolaNeural",
    "es": "es-ES-EstrellaNeural",
    "de": "de-DE-AmalaNeural",
    "fr": "fr-FR-DeniseNeural",
    "pt": "pt-BR-FranciscaNeural",
}

# === Log setup ===
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter(
        "[%(levelname)s][%(asctime)s.%(msecs)03d] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logging.basicConfig(level=logging.INFO, handlers=[console_handler], force=True)
log = logging.getLogger("deepl-rt")

# ---------------------------
# Support: queue -> generator for RealtimeTTS
# ---------------------------
class GeneratorQueue:
    """
    Synchronous bridge between queue and generator for RealtimeTTS.
    
    Converts a queue-based producer-consumer pattern into a generator
    that RealtimeTTS TextToAudioStream can consume. Blocks on queue.get()
    and yields text strings until close() is called.
    
    Attributes:
        q (queue.Queue): Thread-safe queue for text items
        _closed (bool): Flag indicating if queue is closed
    """
    
    def __init__(self):
        """Initialize empty generator queue."""
        self.q = queue.Queue()
        self._closed = False

    def put(self, text: str):
        """
        Add text to queue for generation.
        
        Args:
            text (str): Text string to add to queue
        """
        if not self._closed:
            self.q.put(text)

    def close(self):
        """
        Close queue and signal end of generation.
        
        Puts None sentinel value to signal generator to stop.
        """
        self._closed = True
        self.q.put(None)

    def generator(self):
        """
        Generator that yields text items from queue.
        
        Blocks on queue.get() until items are available. Stops when
        None sentinel is received (after close() is called).
        
        Yields:
            str: Text strings from the queue
        """
        while True:
            item = self.q.get()
            if item is None:
                break
            #print(f"\n[GeneratorQueue] Yielding item: {item}")
            yield item


class LiveTranscriptGUI:
    """
    Simple Tkinter window that displays real-time transcript updates.
    
    Creates a GUI with text areas for each language channel, displaying
    live transcription and translation with configurable highlighting
    for recent updates.
    
    Attributes:
        root (tk.Tk): Main Tkinter window
        _languages (tuple): Language identifiers for each channel
        _highlight_duration_ms (int): Duration to highlight new text (ms)
        _running (bool): Flag indicating if GUI is active
        _texts (dict): Text widgets for each language
        _scroll_y (dict): Scrollbars for each text widget
        _lang_labels (dict): Label widgets for language headers
    """

    def __init__(self, languages: tuple[str, ...], highlight_duration_ms: int = 1500):
        """
        Initialize live transcript GUI.
        
        Args:
            languages (tuple[str, ...]): Language identifiers for channels
            highlight_duration_ms (int, optional): Highlight duration in ms. Defaults to 1500.
        """
        self._languages = languages
        self._highlight_duration_ms = highlight_duration_ms
        self._running = True

        self.root = tk.Tk()
        self.root.title("Realtime Translation")
        self.root.configure(padx=12, pady=12)

        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        fallback_width = max(self.root.winfo_width(), self.root.winfo_reqwidth(), 800)
        target_width = min(max(int(screen_width * 0.6), 900), screen_width)
        target_height = min(max(int(screen_height * 0.65), 600), screen_height)
        x_offset = max((screen_width - target_width) // 2, 0)
        y_offset = max((screen_height - target_height) // 4, 0)
        self.root.minsize(min(target_width, screen_width), min(target_height, screen_height))
        self.root.geometry(f"{target_width}x{target_height}+{x_offset}+{y_offset}")

        controls = tk.Frame(self.root)
        controls.pack(fill="x", pady=(0, 8))
        tk.Button(controls, text="Clean", command=self._clear_transcripts).pack(anchor="e")

        container = tk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self._text_widgets: dict[str, ScrolledText] = {}
        self._update_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._highlight_queue: queue.Queue[tuple[str, str, int]] = queue.Queue()

        for lang in languages:
            frame = tk.LabelFrame(container, text=lang.upper(), padx=8, pady=8)
            frame.pack(fill="both", expand=True, pady=6)

            text_widget = ScrolledText(frame, wrap="word", state="disabled", height=8)
            text_widget.pack(fill="both", expand=True)
            text_widget.tag_configure("base", foreground="#1f2933")
            self._text_widgets[lang] = text_widget

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def enqueue_chunk(self, language: str, chunk: str):
        """
        Add text chunk to update queue for display.
        
        Args:
            language (str): Language identifier for the text widget
            chunk (str): Text chunk to append
        """
        if not chunk:
            return
        if language not in self._languages:
            return
        self._update_queue.put((language, chunk))

    def highlight_sentence(self, language: str, sentence: str, duration_ms: int | None = None):
        """
        Highlight a sentence in the transcript temporarily.
        
        Args:
            language (str): Language identifier for the text widget
            sentence (str): Sentence text to highlight
            duration_ms (int | None, optional): Highlight duration in ms
        """
        if not sentence:
            return
        if language not in self._languages:
            return
        duration = duration_ms if duration_ms is not None else self._highlight_duration_ms
        self._highlight_queue.put((language, sentence.strip(), duration))

    def start(self):
        """
        Start GUI event loop and queue processing.
        
        Begins processing update queue and starts Tkinter mainloop.
        """
        self._process_queue()
        try:
            self.root.mainloop()
        finally:
            self._running = False

    def stop(self):
        """
        Stop GUI and quit event loop.
        
        Safely shuts down the Tkinter window and stops processing.
        """
        self._running = False
        try:
            self.root.after(0, self.root.quit)
        except tk.TclError:
            pass

    def _on_close(self):
        self.stop()

    def _process_queue(self):
        if not self._running:
            return
        try:
            while True:
                lang, chunk = self._update_queue.get_nowait()
                self._append_text(lang, chunk)
        except queue.Empty:
            pass
        try:
            while True:
                lang, sentence, duration = self._highlight_queue.get_nowait()
                self._apply_highlight(lang, sentence, duration)
        except queue.Empty:
            pass
        self.root.after(80, self._process_queue)

    def _append_text(self, language: str, chunk: str):
        widget = self._text_widgets.get(language)
        if widget is None:
            return

        widget.configure(state="normal")
        widget.insert("end", chunk, ("base",))
        widget.configure(state="disabled")
        widget.see("end")

    def _apply_highlight(self, language: str, sentence: str, duration_ms: int):
        widget = self._text_widgets.get(language)
        if widget is None:
            return
        sentence = sentence.strip()
        if not sentence:
            return

        widget.configure(state="normal")
        start_index = widget.search(sentence, "end-1c", "1.0", backwards=True)
        if not start_index:
            widget.configure(state="disabled")
            return

        end_index = f"{start_index}+{len(sentence)}c"
        tag_name = f"tts_highlight_{time.time_ns()}"
        widget.tag_add(tag_name, start_index, end_index)
        widget.tag_configure(tag_name, background="#fff59d")
        widget.configure(state="disabled")

        def clear_highlight():
            try:
                widget.configure(state="normal")
                widget.tag_delete(tag_name)
                widget.configure(state="disabled")
            except tk.TclError:
                pass

        try:
            self.root.after(duration_ms, clear_highlight)
        except tk.TclError:
            pass

    def _clear_transcripts(self):
        while True:
            try:
                self._update_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self._highlight_queue.get_nowait()
            except queue.Empty:
                break

        for widget in self._text_widgets.values():
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")

# ---------------------------
# DeepL Realtime client (async)
# ---------------------------
class DeepLRealtimeClient:
    """
    Async client for DeepL Voice API realtime translation.
    
    Manages WebSocket connection to DeepL Voice API, handles audio streaming
    from microphone, receives translation results, and integrates with TTS
    for audio output.
    
    Attributes:
        api_key (str): DeepL Voice API key
        api_url (str): DeepL API base URL
        source_lang (str | None): Source language code (None for auto-detect)
        target_langs (tuple): Target language codes for translation
        on_target_concluded (Callable): Callback for concluded translations
        _stop (asyncio.Event): Stop signal for async tasks
        _ws (ClientConnection | None): Active WebSocket connection
    """
    
    async def shutdown(self):
        """
        Stop the client and close WebSocket connection.
        
        Sets stop event and safely closes the WebSocket if open.
        """
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
    
    def __init__(
        self,
        api_key: str,
        api_url: str,
        source_lang: str | None,
        target_langs: tuple[str, ...],
        on_target_concluded: Callable[[str, str], None] | None = None,
    ):
        """
        Initialize DeepL realtime client.
        
        Args:
            api_key (str): DeepL Voice API authentication key
            api_url (str): DeepL API base URL
            source_lang (str | None): Source language (None for auto-detection)
            target_langs (tuple[str, ...]): Target languages for translation
            on_target_concluded (Callable | None, optional): Callback for final translations
        """
        self.api_key = api_key
        self.api_url = api_url
        self.source_lang = source_lang
        self.target_langs = target_langs
        self._ws = None
        self._stop = asyncio.Event()
        self._finalized_targets = set()
        self._acc_target = {lang: "" for lang in target_langs}
        self._on_target_concluded = on_target_concluded

    async def get_streaming_uri(self, session: aiohttp.ClientSession) -> str:
        payload = {
            "media_content_type": MEDIA_CONTENT_TYPE,
            "source_language": self.source_lang,
            "source_language_mode": "fixed" if self.source_lang else "auto",
            "target_languages": self.target_langs,
            "early_access_experimental_mode": "enable_early_translation_conclusion",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"DeepL-Auth-Key {self.api_key}",
        }
        log.info("Requesting streaming URI from DeepL...")
        async with session.post(self.api_url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()

        token = data.get("token")
        streaming_url = data.get("streaming_url")
        if not token or not streaming_url:
            raise RuntimeError(f"DeepL: token or streaming URL missing in response: {data}")
        return f"{streaming_url}?{urlencode({'token': token})}"

    async def mic_producer(self, ws: ClientConnection):
        """
        Capture microphone audio and send PCM s16le 16k mono chunks.
        
        Starts a sounddevice input stream, captures audio frames via callback,
        and sends them through the WebSocket connection to DeepL Voice API.
        Uses asyncio.Queue for thread-safe audio transfer.
        
        Args:
            ws (ClientConnection): WebSocket connection to DeepL Voice API
            
        Returns:
            None
            
        Raises:
            asyncio.TimeoutError: If audio queue times out (handled internally)
        """
        log.info("Microphone capture started")
        # sounddevice stream callback -> pushes frames into an asyncio.Queue
        audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        def sd_callback(indata, frames, time_info, status):
            if status:
                log.warning(f"sounddevice status: {status}")
            # indata: float32 if dtype is default; force int16 in the stream
            audio_q.put_nowait(indata.tobytes())

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=FRAMES_PER_CHUNK,
            callback=sd_callback,
            latency="low"
        )
        stream.start()

        CHUNK_DURATION = FRAMES_PER_CHUNK / SAMPLE_RATE
        MIN_INTERVAL   = CHUNK_DURATION / 2.0
        last_send_ts   = time.monotonic() - CHUNK_DURATION  

        try:
            while not self._stop.is_set():
                try:
                    chunk = await asyncio.wait_for(audio_q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                # msg = {"source_data_chunk": {"data": base64.b64encode(chunk).decode("utf-8")}}
                # await ws.send(json.dumps(msg))

                # If there is no backlog (qsize<=1), respect the minimum interval.
                # If there is a backlog, no sleep to recover.
                if audio_q.qsize() <= 1:
                    now = time.monotonic()
                    wait_s = (last_send_ts + MIN_INTERVAL) - now
                    if wait_s > 0:
                        await asyncio.sleep(wait_s)

                msg = {"source_data_chunk": {
                    "data": base64.b64encode(chunk).decode("utf-8")
                }}
                await ws.send(json.dumps(msg))
                last_send_ts = time.monotonic()
        finally:
            log.info("Stopping microphone capture")
            stream.stop()
            stream.close()
            # signal end of data
            try:
                await ws.send(json.dumps({"end_of_source_data": {}}))
            except Exception:
                pass

    async def ws_listener(self, ws: ClientConnection, out_queue: GeneratorQueue):
        """
        Receive translation updates from WebSocket and push to TTS queue.
        
        Listens for both tentative (partial) and concluded (final) translations
        from DeepL Voice API. Accumulates concluded text and feeds it to the
        TTS queue for audio synthesis.
        
        Args:
            ws (ClientConnection): WebSocket connection to DeepL
            out_queue (GeneratorQueue): Queue for pushing translated text to TTS
            
        Returns:
            None
        """
        try:
            full_concluded_target_transcript = {lang: "" for lang in self.target_langs}
            async for raw in ws:
                data = json.loads(raw)

                if "source_transcript_update" in data:
                    st = data["source_transcript_update"]
                    concl = "".join(c["text"] for c in st.get("concluded", []))
                    tent  = "".join(c["text"] for c in st.get("tentative", []))
                    if concl:
                        log.debug(f"[SRC concl] {concl}")
                    if tent:
                        log.debug(f"[SRC tent ] {tent}")

                elif "target_transcript_update" in data:
                    tu = data["target_transcript_update"]
                    lang = tu["language"]
                    concl = "".join(c["text"] for c in tu.get("concluded", []))
                    tent  = "".join(c["text"] for c in tu.get("tentative", []))

                    if concl:
                        text = concl
                        full_concluded_target_transcript[lang] += concl
                        log.info("[DeepL][%s] %s", lang.upper(), full_concluded_target_transcript[lang])
                        out_queue.put(text)
                        if self._on_target_concluded:
                            try:
                                self._on_target_concluded(lang, text)
                            except Exception as callback_error:
                                log.warning("[GUI] Callback error: %s", callback_error)

                elif "end_of_source_transcript" in data:
                    log.info("[DeepL] End of source transcript")

                elif "end_of_target_transcript" in data:
                    lang = data["end_of_target_transcript"]["language"]
                    log.info("[DeepL] Final target transcript received (%s)", lang)
                    self._finalized_targets.add(lang)
                    if len(self._finalized_targets) == len(self.target_langs):
                        break

                elif "acknowledged" in data:
                    # optional
                    pass

                elif "error" in data:
                    log.error(f"[DeepL] Error: {data['error']}")

                else:
                    log.debug("[DeepL] Unknown message: %s", data)

        except ConnectionClosed:
            log.warning("[DeepL] WebSocket closed by server")
        except Exception as ex:
            log.error(f"[DeepL] Listener error: {ex}", exc_info=ex)
        finally:
            # signal output closure to the TTS
            out_queue.close()

    async def run(self, out_queue: GeneratorQueue):
        """
        Main async run loop for DeepL realtime translation.
        
        Establishes WebSocket connection to DeepL Voice API, creates
        listener and producer tasks, and manages their lifecycle.
        
        Args:
            out_queue (GeneratorQueue): Queue to push translated text for TTS
            
        Returns:
            None
            
        Raises:
            RuntimeError: If DEEPL_VOICE_API_KEY is not configured
        """
        if not self.api_key:
            raise RuntimeError("DEEPL_VOICE_API_KEY is not set.")
        async with aiohttp.ClientSession() as session:
            uri = await self.get_streaming_uri(session)
        log.info("Connecting WebSocket to DeepL...")
        async with connect(uri) as ws:
            log.info("Connected to DeepL Realtime")
            self._ws = ws
            listener = asyncio.create_task(self.ws_listener(ws, out_queue))
            producer = asyncio.create_task(self.mic_producer(ws))
            # Wait for the listener (producer stops when stop event is set)
            await listener
            self._stop.set()
            with contextlib.suppress(Exception):
                await producer

# ---------------------------
# TTS engine selection helpers
# ---------------------------
SUPPORTED_TTS_ENGINES = {"system", "azure", "elevenlabs", "openai"}

def create_tts_engine(engine_name: str):
    """
    Create the configured TTS engine with validation.
    
    Factory function that creates and initializes the specified TTS
    engine (Azure, ElevenLabs, OpenAI, or system default) with proper
    credential validation.
    
    Args:
        engine_name (str): Name of TTS engine ("azure", "elevenlabs", "openai", or "system")
        
    Returns:
        Engine: Configured TTS engine instance (AzureEngine, ElevenlabsEngine, etc.)
        
    Raises:
        RuntimeError: If required credentials for selected engine are missing
        ValueError: If engine_name is not supported
    """
    engine_key = (engine_name or "").strip().lower()

    if engine_key == "azure":
        if not AZURE_SPEECH_KEY:
            raise RuntimeError("Set AZURE_SPEECH_KEY to use the Azure TTS engine.")
        if not AZURE_SPEECH_REGION:
            raise RuntimeError("Set AZURE_SPEECH_REGION to use the Azure TTS engine.")

        engine = AzureEngine(
            AZURE_SPEECH_KEY,
            AZURE_SPEECH_REGION,
            rate=AZURE_TTS_RATE,
        )

        voice = AZURE_TTS_VOICE or AZURE_DEFAULT_VOICE_BY_LANG.get(TARGET_LANGUAGE, "en-US-AshleyNeural")
        if voice and hasattr(engine, "set_voice"):
            engine.set_voice(voice)
        return engine

    if engine_key == "elevenlabs":
        if not ELEVENLABS_API_KEY:
            raise RuntimeError("Set ELEVENLABS_API_KEY to use the ElevenLabs TTS engine.")

        engine = ElevenlabsEngine(
            api_key=ELEVENLABS_API_KEY,
            voice=ELEVENLABS_VOICE,
            model=ELEVENLABS_MODEL,
            id=ELEVENLABS_VOICE_ID,
        )
        if ELEVENLABS_VOICE and hasattr(engine, "set_voice"):
            engine.set_voice(ELEVENLABS_VOICE)
        return engine

    if engine_key == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("Set OPENAI_API_KEY to use the OpenAI TTS engine.")

        engine = OpenAIEngine(
            voice=OPENAI_TTS_VOICE,
            model=OPENAI_TTS_MODEL,
        )
        if OPENAI_TTS_VOICE and hasattr(engine, "set_voice"):
            engine.set_voice(OPENAI_TTS_VOICE)
        return engine

    if engine_key == "system":
        engine = SystemEngine()
        system_voice = os.getenv("SYSTEM_TTS_VOICE")
        if system_voice and hasattr(engine, "set_voice"):
            engine.set_voice(system_voice)
        return engine

    raise ValueError(
        f"Unsupported TTS engine '{engine_name}'. Choose one of: {', '.join(sorted(SUPPORTED_TTS_ENGINES))}."
    )

# ---------------------------
# Main: orchestrate DeepL -> queue -> RealtimeTTS
# ---------------------------
def main():
    if not DEEPL_VOICE_API_KEY:
        log.error("Set the DEEPL_VOICE_API_KEY environment variable before running.")
        sys.exit(1)
    if TTS_ENGINE_NAME not in SUPPORTED_TTS_ENGINES:
        log.error(
            "Unsupported TTS_ENGINE '%s'. Choose one of: %s.",
            TTS_ENGINE_NAME,
            ", ".join(sorted(SUPPORTED_TTS_ENGINES)),
        )
        sys.exit(1)

    try:
        engine = create_tts_engine(TTS_ENGINE_NAME)
    except (RuntimeError, ValueError) as err:
        log.error(str(err))
        sys.exit(1)

    log.info("Using '%s' TTS engine", TTS_ENGINE_NAME)

    def on_word(w):
        log.debug("[TTS word] %s", w.word)

    stream = TextToAudioStream(engine, log_characters=False, language=TARGET_LANGUAGE)

    # Queue for translated text
    out_queue = GeneratorQueue()

    gui_languages = (TARGET_LANGUAGE,)
    gui = LiveTranscriptGUI(gui_languages)

    def on_sentence_synthesized(sentence: str):
        gui.highlight_sentence(TARGET_LANGUAGE, sentence)

    # Start DeepL in a separate thread (asyncio event loop)
    client = DeepLRealtimeClient(
        api_key=DEEPL_VOICE_API_KEY,
        api_url=DEEPL_VOICE_API_URL,
        source_lang=SOURCE_LANGUAGE,
        target_langs=gui_languages,
        on_target_concluded=gui.enqueue_chunk,
    )

    def run_asyncio_client():
        asyncio.run(client.run(out_queue))

    t = threading.Thread(target=run_asyncio_client, daemon=True)
    t.start()

    log.info(
        "[TTS] Speak in %s -> translation into %s with DeepL; synthesis with different TTS engine...",
        SOURCE_LANGUAGE or "auto",
        TARGET_LANGUAGE,
    )
    # Feed the TTS with the queue
    stream.feed(out_queue.generator())

    # Stop flag for playback
    stop_playback = threading.Event()

    def play_stream():
        try:
            stream.play(
                fast_sentence_fragment=True,
                fast_sentence_fragment_allsentences=True,
                fast_sentence_fragment_allsentences_multiple=True,
                log_synthesized_text=True,
                # buffer_threshold_seconds=2,
                # minimum_sentence_length = 10,
                # context_size=5,
                # context_size_look_overhead=10,
                force_first_fragment_after_words=5,
                on_sentence_synthesized=on_sentence_synthesized,
                tokenizer="nltk",
                language=TARGET_LANGUAGE
            )
        except Exception as e:
            pass

    play_thread = threading.Thread(target=play_stream, daemon=True)
    play_thread.start()

    try:
        gui.start()
    except KeyboardInterrupt:
        log.info("[STOP] Interrupted by user (Ctrl+C). Cleaning up...")
    finally:
        out_queue.close()
        stop_playback.set()
        with contextlib.suppress(Exception):
            asyncio.run(client.shutdown())
        gui.stop()
        if play_thread.is_alive():
            stop_fn = getattr(stream, "stop", None)
            if callable(stop_fn):
                with contextlib.suppress(Exception):
                    stop_fn()
            play_thread.join(timeout=1.0)
        if t.is_alive():
            t.join(timeout=1.0)

    log.info("[OK] Completed.")

if __name__ == "__main__":
    import contextlib
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
