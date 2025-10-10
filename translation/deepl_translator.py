"""
DeepL Voice API real-time translation service.

This module provides WebSocket-based real-time translation using DeepL Voice API
with automatic reconnection, keepalive, and integration with Azure TTS.
"""

import asyncio
import base64
import json
import logging
import queue
import threading
import time
import traceback
from urllib.parse import urlencode

import aiohttp
import websockets

from utils.timing import log_timing


logger = logging.getLogger("translator_bridge")


class DeepLTranslator:
    """
    Real-time translator using DeepL Voice API with Azure TTS output.
    
    Manages WebSocket connection to DeepL for streaming audio translation,
    with automatic reconnection, keepalive mechanism, and integration with
    Azure Text-to-Speech for audio output.
    
    Attributes:
        label (str): Label for logging identification
        channel (str): Channel identifier ("A" or "B")
        deepl_api_key (str): DeepL API authentication key
        from_lang (str): Source language code
        to_lang (str): Target language code
        capture_pipe: Reference to audio capture pipe
        running (bool): Service running state
        phrase_count (int): Number of translated phrases
        tts: Azure TTS instance for speech synthesis
        websocket: Active WebSocket connection
        websocket_task: Async task for WebSocket management
        audio_send_task: Async task for audio sending
        listener_task: Async task for message listening
        loop: Asyncio event loop
        session: Aiohttp client session
        audio_queue: Queue for audio data to send to DeepL
        last_spoken_text (str): Last text sent to TTS (for deduplication)
    """
    
    def __init__(self, deepl_api_key, azure_key, azure_region, from_lang, to_lang, 
                 voice_name, push_stream, output_device_idx, channel, label, 
                 capture_pipe=None):
        """
        Initialize DeepL translator with Azure TTS integration.
        
        Args:
            deepl_api_key (str): DeepL API key
            azure_key (str): Azure Speech Service key
            azure_region (str): Azure region
            from_lang (str): Source language code (e.g., "it", "en")
            to_lang (str): Target language code
            voice_name (str): Azure neural voice name
            push_stream: Azure push stream (unused, kept for compatibility)
            output_device_idx (int or None): Audio output device index
            channel (str): Channel identifier ("A" or "B")
            label (str): Label for logging
            capture_pipe (CapturePipe, optional): Audio capture pipe reference
        """
        from tts.azure_tts import FastStreamingTTS
        
        self.label = label
        self.channel = channel
        self.deepl_api_key = deepl_api_key
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.push_stream = push_stream
        self.capture_pipe = capture_pipe
        self.running = False
        self.phrase_count = 0
        
        # Initialize Azure TTS
        from config.settings import SPEECH_RATE
        self.tts = FastStreamingTTS(
            azure_key, azure_region, voice_name, output_device_idx,
            f"{label}-TTS", SPEECH_RATE,
            on_playback_start=self._on_translation_start
        )
        
        # WebSocket components
        self.websocket = None
        self.websocket_task = None
        self.audio_send_task = None
        self.listener_task = None
        self.loop = None
        self.session = None
        
        # Audio queue for DeepL
        self.audio_queue = queue.Queue(maxsize=100)
        self.last_spoken_text = ""
        self.last_speech_start_time = None  # For timing tracking
    
    def _on_translation_start(self):
        """
        Callback when TTS starts playback.
        
        Stops keyboard sound playback if active in call center mode.
        """
        if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
            if self.capture_pipe.keyboard_playing:
                self.capture_pipe.keyboard_stop_event.set()
                logger.info(f"{self.label} TTS started -> keyboard stopped")
    
    async def _get_streaming_uri(self):
        """
        Get WebSocket URI from DeepL API.
        
        Requests a streaming session token and URL from DeepL REST API.
        
        Returns:
            str: WebSocket URI with authentication token
            
        Raises:
            ValueError: If API response is invalid
            aiohttp.ClientError: If HTTP request fails
        """
        request_payload = {
            "media_content_type": "audio/pcm;encoding=s16le;rate=16000",
            "source_language": self.from_lang,
            "source_language_mode": "fixed",
            "target_languages": [self.to_lang],
        }
        
        async with self.session.post(
            self._get_api_url(),
            json=request_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"DeepL-Auth-Key {self.deepl_api_key}",
            },
        ) as response:
            response.raise_for_status()
            response_data = await response.json()
        
        stream_token = response_data.get("token")
        streaming_url = response_data.get("streaming_url")
        
        if not stream_token or not streaming_url:
            raise ValueError(f"DeepL API error: {response_data.get('message')}")
        
        return f"{streaming_url}?{urlencode({'token': stream_token})}"
    
    def _get_api_url(self):
        """
        Get DeepL API URL from configuration.
        
        Returns:
            str: DeepL API URL
        """
        from config.settings import DEEPL_API_URL
        return DEEPL_API_URL
    
    async def _websocket_listener(self):
        """
        Listen for translation messages from DeepL WebSocket.
        
        Processes incoming messages including source transcripts, target translations,
        and error messages. Sends translations to TTS for speech synthesis.
        """
        try:
            full_concluded_target = ""
            message_count = 0
            acknowledged_count = 0
            
            async for message in self.websocket:
                if not self.running:
                    break
                
                message_count += 1
                
                try:
                    data = json.loads(message)
                    
                    # Skip logging for "acknowledged" messages (they're too frequent)
                    if "acknowledged" in data:
                        acknowledged_count += 1
                        # Only log every 500 acknowledged messages
                        if acknowledged_count % 500 == 0:
                            logger.debug(f"{self.label} Acknowledged messages: {acknowledged_count}")
                        continue
                    
                    # Log other messages for debugging (first 5 and every 50th)
                    if message_count <= 5 or message_count % 50 == 0:
                        logger.debug(f"{self.label} WS Message #{message_count}: {list(data.keys())}")
                    
                except json.JSONDecodeError:
                    logger.warning(f"{self.label} Invalid JSON received: {message[:100]}")
                    continue
                
                # Source transcript (for debugging)
                if "source_transcript_update" in data:
                    update = data["source_transcript_update"]
                    concluded = "".join(c["text"] for c in update.get("concluded", []))
                    if concluded:
                        logger.info(f"{self.label} Source: {concluded}")
                
                # Target translation
                elif "target_transcript_update" in data:
                    update = data["target_transcript_update"]
                    concluded = "".join(c["text"] for c in update.get("concluded", []))
                    
                    if concluded and concluded != self.last_spoken_text:
                        # Log timing for translation received
                        ts_translation = log_timing(
                            logger, self.label, "TRANSLATION RECEIVED",
                            getattr(self, 'last_speech_start_time', None),
                            f"Text: '{concluded[:50]}...'" if len(concluded) > 50 else f"Text: '{concluded}'"
                        )
                        
                        full_concluded_target += concluded
                        self.last_spoken_text = concluded
                        
                        # Stop keyboard sound when translation arrives
                        if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                            self.capture_pipe.keyboard_stop_event.set()
                        
                        # Send to TTS (stores timestamp for delay calculation)
                        self.tts.last_translation_time = ts_translation
                        
                        logger.info(f"{self.label} <- [{self.to_lang}] {concluded}")
                        self.tts.speak(concluded)
                        self.phrase_count += 1
                
                # End of transcript
                elif "end_of_target_transcript" in data:
                    logger.info(f"{self.label} Translation complete")
                    full_concluded_target = ""
                
                # Error handling
                elif "error" in data:
                    error_msg = data.get("error", {})
                    logger.error(f"{self.label} DeepL error: {error_msg}")
                    if isinstance(error_msg, dict) and "message" in error_msg:
                        logger.error(f"{self.label}    Details: {error_msg['message']}")
        
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"{self.label} WebSocket closed (code={getattr(e, 'code', 'N/A')})")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"{self.label} Connection closed unexpectedly: {e}")
        except Exception as e:
            if self.running:
                logger.error(f"{self.label} Listener error: {e}")
                logger.debug(traceback.format_exc())
    
    async def _audio_sender(self):
        """
        Send audio data to DeepL WebSocket with keepalive.
        
        Reads audio from queue and sends to WebSocket as JSON messages with
        base64-encoded audio data. Implements keepalive by sending silence
        if no audio for 5 seconds.
        """
        try:
            sequence = 0
            last_send_time = asyncio.get_event_loop().time()
            keepalive_interval = 5.0  # Send something every 5 seconds
            audio_chunks_sent = 0
            
            while self.running:
                try:
                    # Try to get audio from queue with timeout
                    try:
                        audio_chunk = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None, self.audio_queue.get, True, 0.1
                            ),
                            timeout=0.5
                        )
                        
                        if audio_chunk is None:
                            break
                        
                        # Send audio as JSON with base64 encoding
                        message = json.dumps({
                            "source_data_chunk": {
                                "data": base64.b64encode(audio_chunk).decode("utf-8")
                            }
                        })
                        
                        await self.websocket.send(message)
                        sequence += 1
                        audio_chunks_sent += 1
                        last_send_time = asyncio.get_event_loop().time()
                        
                        # Log first chunk and every 1000 chunks (less verbose)
                        if audio_chunks_sent == 1:
                            logger.info(f"{self.label} First audio chunk sent ({len(audio_chunk)} bytes)")
                        elif audio_chunks_sent % 1000 == 0:
                            logger.debug(f"{self.label} Audio chunks sent: {audio_chunks_sent}")
                        
                    except queue.Empty:
                        # Check if keepalive needed
                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_send_time >= keepalive_interval:
                            # Send silence as keepalive (320 bytes = 20ms at 16kHz)
                            silence = b'\x00' * 320
                            message = json.dumps({
                                "source_data_chunk": {
                                    "data": base64.b64encode(silence).decode("utf-8")
                                }
                            })
                            await self.websocket.send(message)
                            last_send_time = current_time
                            logger.debug(f"{self.label} Keepalive sent")
                        await asyncio.sleep(0.01)
                        continue
                    
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"{self.label} Connection closed while sending: {e}")
                    break
                except Exception as e:
                    logger.error(f"{self.label} Audio sender error: {e}")
                    break
            
            # Signal end of audio stream
            if self.websocket:
                try:
                    await self.websocket.send(json.dumps({"end_of_source_data": {}}))
                    logger.info(f"{self.label} Audio stream ended")
                except websockets.exceptions.ConnectionClosed:
                    logger.debug(f"{self.label} WebSocket already closed")
                except Exception as e:
                    logger.debug(f"{self.label} Could not send end marker: {e}")
        
        except Exception as e:
            if self.running:
                logger.error(f"{self.label} Sender error: {e}")
    
    async def _run_websocket(self):
        """
        Manage WebSocket connection with auto-reconnect.
        
        Maintains WebSocket connection to DeepL with exponential backoff
        reconnection strategy. Creates listener and sender tasks.
        """
        reconnect_delay = 2  # seconds
        max_reconnect_delay = 30
        
        # Create HTTP session (one for entire lifecycle)
        self.session = aiohttp.ClientSession()
        
        try:
            while self.running:
                try:
                    logger.info(f"{self.label} Connecting to DeepL WebSocket...")
                    
                    # Get streaming URI
                    ws_uri = await self._get_streaming_uri()
                    
                    # Connect to WebSocket
                    async with websockets.connect(
                        ws_uri,
                        ping_interval=15,
                        ping_timeout=10,
                        close_timeout=5
                    ) as websocket:
                        self.websocket = websocket
                        logger.info(f"{self.label} Connected to DeepL!")
                        
                        # Reset reconnect delay on successful connection
                        reconnect_delay = 2
                        
                        # Create listener and sender tasks
                        self.listener_task = asyncio.create_task(self._websocket_listener())
                        self.audio_send_task = asyncio.create_task(self._audio_sender())
                        
                        # Wait for tasks to complete
                        await asyncio.gather(
                            self.listener_task,
                            self.audio_send_task,
                            return_exceptions=True
                        )
                
                except websockets.exceptions.ConnectionClosed as e:
                    if self.running:
                        logger.warning(f"{self.label} Connection closed, reconnecting in {reconnect_delay}s...")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    else:
                        break
                
                except websockets.exceptions.WebSocketException as e:
                    if self.running:
                        logger.error(f"{self.label} WebSocket error: {e}")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    else:
                        break
                
                except Exception as e:
                    logger.error(f"{self.label} Unexpected error: {e}")
                    if self.running:
                        await asyncio.sleep(reconnect_delay)
                    else:
                        break
        
        finally:
            if self.session:
                await self.session.close()
                logger.info(f"{self.label} Session closed")
    
    def start(self):
        """
        Start DeepL translator service.
        
        Launches asyncio event loop in separate thread for WebSocket management.
        """
        try:
            self.running = True
            
            # Start WebSocket thread with asyncio loop
            def run_async_loop():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                try:
                    self.loop.run_until_complete(self._run_websocket())
                finally:
                    self.loop.close()
            
            self.websocket_thread = threading.Thread(target=run_async_loop, daemon=True)
            self.websocket_thread.start()
            
            logger.info(f"SUCCESS {self.label} DeepL Translator started ({self.from_lang} -> {self.to_lang})")
        
        except Exception as e:
            logger.error(f"{self.label} Start error: {e}")
            raise
    
    def push_audio(self, audio_bytes):
        """
        Queue audio data to send to DeepL.
        
        Args:
            audio_bytes (bytes): Raw PCM16 audio data
        """
        if self.running:
            try:
                self.audio_queue.put_nowait(audio_bytes)
            except queue.Full:
                pass  # Discard if queue full
    
    def stop(self):
        """
        Stop translator service and cleanup resources.
        
        Cancels async tasks, closes WebSocket, and shuts down TTS.
        """
        try:
            self.running = False
            
            # Cancel asyncio tasks if active
            if self.loop and self.loop.is_running():
                if self.listener_task and not self.listener_task.done():
                    self.listener_task.cancel()
                if self.audio_send_task and not self.audio_send_task.done():
                    self.audio_send_task.cancel()
            
            # Signal end of audio
            if self.audio_queue:
                try:
                    self.audio_queue.put_nowait(None)
                except:
                    pass
            
            # Stop TTS
            self.tts.shutdown()
            
            logger.info(f"{self.label} STOPPED DeepL Translator. Phrases: {self.phrase_count}")
        except Exception as e:
            logger.error(f"{self.label} Stop error: {e}")
