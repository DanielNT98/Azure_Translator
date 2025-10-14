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
        # Import TTS based on configuration (RealtimeTTS or standard)
        from config.settings import SPEECH_RATE, USE_REALTIME_TTS
        
        if USE_REALTIME_TTS:
            from tts.realtime_azure_tts import RealtimeAzureTTS as TTSEngine
            logger.info(f"{label} Using RealtimeTTS for ultra-low latency")
        else:
            from tts.azure_tts import FastStreamingTTS as TTSEngine
            logger.info(f"{label} Using standard Azure TTS")
        
        self.label = label
        self.channel = channel
        self.deepl_api_key = deepl_api_key
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.push_stream = push_stream
        self.capture_pipe = capture_pipe
        self.running = False
        self.phrase_count = 0
        
        # Initialize TTS (RealtimeTTS or standard Azure TTS)
        self.tts = TTSEngine(
            azure_key, azure_region, voice_name, output_device_idx,
            f"{label}-TTS", SPEECH_RATE,
            on_playback_start=self._on_translation_start
        )
        
        # WebSocket components
        self.websocket = None
        self.websocket_task = None
        self.audio_send_task = None
        self.listener_task = None
        self.buffer_checker_task = None
        self.loop = None
        self.session = None
        
        # Audio queue for DeepL
        self.audio_queue = queue.Queue(maxsize=100)
        self.last_spoken_text = ""
        self.last_speech_start_time = None  # For timing tracking
        
        # Translation buffer for smooth TTS streaming
        self.translation_buffer = ""  # Accumulates chunks for smoother TTS output
        self.min_buffer_length = 15  # Minimum characters before flushing
        self.max_buffer_length = 30  # Maximum characters before forced flush
        self.buffer_timeout = 2.0  # Seconds before forced flush
        self.last_buffer_update_time = None  # Track when buffer was last updated
    
    def _on_translation_start(self):
        """
        Callback when TTS starts playback.
        
        Stops keyboard sound playback if active in call center mode.
        """
        if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
            if self.capture_pipe.keyboard_playing:
                self.capture_pipe.keyboard_stop_event.set()
                logger.info(f"{self.label} TTS started -> keyboard stopped")
    
    def _flush_translation_buffer(self, force=False):
        """
        Flush accumulated translation buffer to TTS with hybrid strategy.
        
        Sends text to TTS when:
        1. Buffer contains a sentence delimiter (., !, ?, ;, :, ,) AND meets minimum length
        2. Buffer exceeds maximum length (50 chars) - prevents buffer overflow
        3. Buffer timeout expired (2s) - prevents long silences
        4. Force flag is True (end of transcript)
        
        Args:
            force (bool): If True, flush entire buffer regardless of conditions
            
        Returns:
            bool: True if text was sent to TTS, False otherwise
        """
        if not self.translation_buffer:
            return False
        
        buffer_length = len(self.translation_buffer)
        current_time = time.time()
        
        # Define sentence delimiters
        delimiters = ['.', '!', '?', ';', ':', ",", "..."]
        
        # CONDITION 1: Force flush (end of transcript or explicit stop)
        if force:
            text_to_send = self.translation_buffer.strip()
            if text_to_send:
                logger.info(f"{self.label} <- [{self.to_lang}] {text_to_send} [FORCE]")
                self.tts.speak(text_to_send)
                self.phrase_count += 1
                self.translation_buffer = ""
                self.last_buffer_update_time = None
                return True
            return False
        
        # CONDITION 2: Max buffer length exceeded (safety limit)
        if buffer_length > self.max_buffer_length:
            text_to_send = self.translation_buffer.strip()
            logger.info(f"{self.label} <- [{self.to_lang}] {text_to_send} [MAX LENGTH: {buffer_length} chars]")
            self.tts.speak(text_to_send)
            self.phrase_count += 1
            self.translation_buffer = ""
            self.last_buffer_update_time = None
            return True
        
        # CONDITION 3: Timeout expired (no updates for 2 seconds)
        if self.last_buffer_update_time and (current_time - self.last_buffer_update_time) > self.buffer_timeout:
            text_to_send = self.translation_buffer.strip()
            if text_to_send:
                timeout_duration = current_time - self.last_buffer_update_time
                logger.info(f"{self.label} <- [{self.to_lang}] {text_to_send} [TIMEOUT: {timeout_duration:.1f}s]")
                self.tts.speak(text_to_send)
                self.phrase_count += 1
                self.translation_buffer = ""
                self.last_buffer_update_time = None
                return True
        
        # CONDITION 4: Natural delimiter found (NO minimum length requirement!)
        # Find the FIRST complete sentence (first delimiter after some text)
        first_delimiter_pos = -1
        first_delimiter = None
        
        # DEBUG: Log buffer content
        logger.debug(f"{self.label} Checking buffer for delimiters: '{self.translation_buffer}' ({buffer_length} chars)")
        
        for delimiter in delimiters:
            pos = self.translation_buffer.find(delimiter)
            if pos >= 0:  # Found a delimiter
                logger.debug(f"{self.label}   Found '{delimiter}' at position {pos}")
                # Check if this is earlier than our current earliest
                if first_delimiter_pos == -1 or pos < first_delimiter_pos:
                    first_delimiter_pos = pos
                    first_delimiter = delimiter
                    logger.debug(f"{self.label}   → Selected '{delimiter}' as first delimiter (pos={pos})")
        
        # If we found a delimiter, send immediately (no minimum length!)
        if first_delimiter_pos >= 0:
            # Extract text up to and including the delimiter
            text_to_send = self.translation_buffer[:first_delimiter_pos + len(first_delimiter)].strip()
            
            # Check if text is meaningful (not just delimiter or whitespace)
            # Strip all delimiters to check for actual content
            text_without_delimiters = text_to_send
            for delim in delimiters:
                text_without_delimiters = text_without_delimiters.replace(delim, '')
            
            # Only send if there's actual text content (not just delimiters/spaces)
            if text_to_send and text_without_delimiters.strip():
                logger.info(f"{self.label} <- [{self.to_lang}] {text_to_send} [DELIMITER: '{first_delimiter}']")
                self.tts.speak(text_to_send)
                self.phrase_count += 1
                
                # Keep remaining text in buffer (skip past the delimiter)
                self.translation_buffer = self.translation_buffer[first_delimiter_pos + len(first_delimiter):].lstrip()
                # Don't reset timer if there's still text in buffer
                if not self.translation_buffer:
                    self.last_buffer_update_time = None
                return True
            else:
                # Skip this delimiter and remove it from buffer
                logger.debug(f"{self.label} Skipping delimiter-only content: '{text_to_send}'")
                self.translation_buffer = self.translation_buffer[first_delimiter_pos + len(first_delimiter):].lstrip()
                # Try again with remaining buffer
                if self.translation_buffer:
                    return self._flush_translation_buffer(force=False)
                return False
        
        return False
    
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
        from config.settings import DEEPL_EXPERIMENTAL_MODE
        
        request_payload = {
            "media_content_type": "audio/pcm;encoding=s16le;rate=16000",
            "source_language": self.from_lang,
            "source_language_mode": "fixed",
            "target_languages": [self.to_lang],
        }
        
        # Add experimental mode for lower latency (may cause hallucinations)
        if DEEPL_EXPERIMENTAL_MODE:
            request_payload["early_access_experimental_mode"] = "enable_early_translation_conclusion"
            logger.info(f"{self.label} EXPERIMENTAL MODE enabled (lower latency, potential hallucinations)")
        
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
    
    async def _periodic_buffer_checker(self):
        """
        Independent task to check buffer timeout every 0.5 seconds.
        
        Runs continuously while WebSocket is connected, checking if buffer
        has exceeded timeout threshold and flushing if needed.
        """
        logger.debug(f"{self.label} Periodic buffer checker started")
        try:
            while self.running and self.websocket:
                await asyncio.sleep(0.5)
                
                # Check if buffer has timed out
                if self.translation_buffer and self.last_buffer_update_time:
                    current_time = time.time()
                    time_since_update = current_time - self.last_buffer_update_time
                    
                    if time_since_update > self.buffer_timeout:
                        logger.debug(
                            f"{self.label} 🔔 Periodic timeout triggered "
                            f"(buffer age: {time_since_update:.1f}s, threshold: {self.buffer_timeout}s)"
                        )
                        self._flush_translation_buffer(force=False)
        except Exception as e:
            logger.error(f"{self.label} Periodic buffer checker error: {e}")
            # Re-raise to trigger reconnection
            raise
    
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
                        
                        # Store timestamp for TTS delay calculation
                        self.tts.last_translation_time = ts_translation
                        
                        # Accumulate in buffer instead of sending directly
                        self.translation_buffer += concluded
                        self.last_buffer_update_time = time.time()  # Track when buffer was updated
                        logger.debug(f"{self.label} Buffer += '{concluded}' (total: {len(self.translation_buffer)} chars)")
                        
                        # Try to flush buffer (checks delimiter, max length, timeout)
                        self._flush_translation_buffer(force=False)
                
                # End of transcript
                elif "end_of_target_transcript" in data:
                    logger.info(f"{self.label} Translation complete - flushing remaining buffer")
                    
                    # Flush any remaining text in buffer
                    self._flush_translation_buffer(force=True)
                    
                    full_concluded_target = ""
                    self.translation_buffer = ""  # Clear buffer at end of transcript
                
                # Error handling
                elif "error" in data:
                    error_msg = data.get("error", {})
                    logger.error(f"{self.label} DeepL error: {error_msg}")
                    if isinstance(error_msg, dict) and "message" in error_msg:
                        logger.error(f"{self.label}    Details: {error_msg['message']}")
        
        except websockets.exceptions.ConnectionClosed as e:
            # Flush buffer before disconnecting
            if self.translation_buffer.strip():
                logger.warning(f"{self.label} Flushing buffer on disconnect: '{self.translation_buffer.strip()}'")
                self._flush_translation_buffer(force=True)
            
            logger.warning(f"{self.label} WebSocket closed (code={getattr(e, 'code', 'N/A')})")
            # Re-raise to trigger reconnection in main loop
            raise
            
        except websockets.exceptions.ConnectionClosedError as e:
            # Flush buffer before disconnecting
            if self.translation_buffer.strip():
                logger.warning(f"{self.label} Flushing buffer on error: '{self.translation_buffer.strip()}'")
                self._flush_translation_buffer(force=True)
            
            logger.warning(f"{self.label} Connection closed unexpectedly: {e}")
            # Re-raise to trigger reconnection in main loop
            raise
            
        except Exception as e:
            if self.running:
                # Flush buffer before error handling
                if self.translation_buffer.strip():
                    logger.warning(f"{self.label} Flushing buffer on error: '{self.translation_buffer.strip()}'")
                    self._flush_translation_buffer(force=True)
                
                logger.error(f"{self.label} Listener error: {e}")
                logger.debug(traceback.format_exc())
                # Re-raise to trigger reconnection in main loop
                raise
    
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
                    # Re-raise to trigger reconnection
                    raise
                except Exception as e:
                    logger.error(f"{self.label} Audio sender error: {e}")
                    # Re-raise to trigger reconnection
                    raise
            
            # Force flush any remaining buffer before ending stream
            if self.translation_buffer.strip():
                logger.info(f"{self.label} Flushing final buffer: '{self.translation_buffer.strip()}'")
                self._flush_translation_buffer(force=True)
            
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
    
    async def _keepalive_pinger(self):
        """
        Send periodic keep-alive pings to maintain WebSocket connection.
        
        Sends empty audio packets every 5 seconds when no real audio is being sent.
        This prevents the WebSocket from timing out during silence periods.
        """
        try:
            ping_interval = 5.0  # Send keep-alive every 5 seconds
            last_ping_time = time.time()
            
            while self.running and self.websocket:
                try:
                    current_time = time.time()
                    
                    # Check if it's time to send a keep-alive
                    if current_time - last_ping_time >= ping_interval:
                        # Send a small silent audio packet as keep-alive
                        # This is more reliable than websocket.ping() for some servers
                        silent_packet = b'\x00' * 160  # 10ms of silence at 16kHz
                        message = json.dumps({
                            "audio": {
                                "data": list(silent_packet),
                                "format": "pcm_s16le_16khz_mono"
                            }
                        })
                        
                        # Send keep-alive (will raise ConnectionClosed if websocket closed)
                        if self.websocket:
                            await self.websocket.send(message)
                            last_ping_time = current_time
                            logger.debug(f"{self.label} Keep-alive ping sent")
                    
                    await asyncio.sleep(1.0)  # Check every second
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.debug(f"{self.label} Keep-alive stopped: connection closed")
                    # Re-raise to trigger reconnection
                    raise
                except Exception as e:
                    logger.debug(f"{self.label} Keep-alive error: {e}")
                    # Re-raise to trigger reconnection
                    raise
        
        except Exception as e:
            logger.error(f"{self.label} Keep-alive task error: {e}")
            # Re-raise to trigger reconnection
            raise
    
    async def _run_websocket(self):
        """
        Manage WebSocket connection with auto-reconnect.
        
        Maintains WebSocket connection to DeepL with exponential backoff
        reconnection strategy. Creates listener and sender tasks.
        """
        reconnect_delay = 1  # seconds - start faster
        max_reconnect_delay = 15  # seconds - lower max (was 30)
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        # Create HTTP session (one for entire lifecycle)
        self.session = aiohttp.ClientSession()
        
        try:
            while self.running:
                try:
                    logger.info(f"{self.label} Connecting to DeepL WebSocket...")
                    
                    # Get streaming URI
                    ws_uri = await self._get_streaming_uri()
                    
                    # Connect to WebSocket with reinforced settings
                    async with websockets.connect(
                        ws_uri,
                        ping_interval=10,       # Send ping every 10s (was 15s) - keep connection alive
                        ping_timeout=20,        # Wait 20s for pong (was 10s) - more tolerant
                        close_timeout=10,       # Wait 10s for close handshake (was 5s)
                        max_size=10_000_000,    # 10MB max message size (prevent size errors)
                        max_queue=1000,         # Large message queue (prevent overflow)
                        compression=None,       # Disable compression for speed
                        open_timeout=30,        # 30s timeout for initial connection
                    ) as websocket:
                        self.websocket = websocket
                        logger.info(f"{self.label} Connected to DeepL!")
                        
                        # Reset reconnect delay and failure counter on successful connection
                        reconnect_delay = 1
                        consecutive_failures = 0
                        logger.info(f"{self.label} Connection stable, reset backoff timer")
                        
                        # Create listener, sender, and buffer checker tasks
                        # Note: Using websockets built-in ping (ping_interval=10) instead of custom keep-alive
                        # Custom keep-alive with silent audio causes DeepL to reject connection (error 400)
                        self.listener_task = asyncio.create_task(self._websocket_listener())
                        self.audio_send_task = asyncio.create_task(self._audio_sender())
                        self.buffer_checker_task = asyncio.create_task(self._periodic_buffer_checker())
                        
                        # Wait for tasks to complete - if any task raises an exception, it will propagate
                        try:
                            await asyncio.gather(
                                self.listener_task,
                                self.audio_send_task,
                                self.buffer_checker_task,
                            )
                        except Exception as task_error:
                            # One of the tasks failed - cancel others and let exception propagate
                            logger.debug(f"{self.label} Task failed: {task_error}, cancelling others...")
                            
                            # Cancel all remaining tasks
                            for task in [self.listener_task, self.audio_send_task, self.buffer_checker_task]:
                                if task and not task.done():
                                    task.cancel()
                            
                            # Re-raise to trigger reconnection logic
                            raise
                
                except websockets.exceptions.ConnectionClosed as e:
                    # Force flush buffer before reconnecting (prevent lost translations)
                    if self.translation_buffer.strip():
                        logger.warning(f"{self.label} Flushing buffer before reconnect: '{self.translation_buffer.strip()}'")
                        self._flush_translation_buffer(force=True)
                    
                    consecutive_failures += 1
                    
                    if self.running:
                        if consecutive_failures >= max_consecutive_failures:
                            logger.error(f"{self.label} Too many consecutive failures ({consecutive_failures}), waiting longer...")
                            reconnect_delay = max_reconnect_delay
                        
                        logger.warning(f"{self.label} Connection closed (code={e.code}), reconnecting in {reconnect_delay}s... (attempt {consecutive_failures})")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)  # Gentler backoff (1.5x vs 2x)
                    else:
                        break
                
                except websockets.exceptions.WebSocketException as e:
                    # Force flush buffer before reconnecting (prevent lost translations)
                    if self.translation_buffer.strip():
                        logger.warning(f"{self.label} Flushing buffer on error: '{self.translation_buffer.strip()}'")
                        self._flush_translation_buffer(force=True)
                    
                    consecutive_failures += 1
                    
                    if self.running:
                        logger.error(f"{self.label} WebSocket error: {e} (attempt {consecutive_failures})")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
                    else:
                        break
                
                except Exception as e:
                    # Force flush buffer on unexpected errors
                    if self.translation_buffer.strip():
                        logger.warning(f"{self.label} Flushing buffer on unexpected error: '{self.translation_buffer.strip()}'")
                        self._flush_translation_buffer(force=True)
                    
                    consecutive_failures += 1
                    
                    logger.error(f"{self.label} Unexpected error: {e} (attempt {consecutive_failures})")
                    if self.running:
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
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
            
            # Flush any remaining translation buffer before stopping
            if self.translation_buffer:
                logger.info(f"{self.label} Flushing remaining buffer on stop: '{self.translation_buffer}'")
                self._flush_translation_buffer(force=True)
            
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
