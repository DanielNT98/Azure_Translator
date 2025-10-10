"""
Azure Translator Module
Integrates Azure Speech Translation API with BalancedChunker for real-time translation.
"""

import logging
import azure.cognitiveservices.speech as speechsdk
from typing import Optional
from config.settings import (
    SPEECH_KEY, SPEECH_REGION, FINAL_SEG_MS, 
    USE_PARTIALS
)
from translation.chunker import BalancedChunker

log = logging.getLogger(__name__)


class AzureTranslator:
    """
    Azure-based translator using Speech Translation API.
    Handles continuous recognition with partial and final results.
    """
    
    def __init__(
        self,
        from_lang: str,
        to_lang: str,
        voice_name: str,
        push_stream: speechsdk.audio.PushAudioInputStream,
        tts,
        channel: str,
        label: str,
        turn_manager=None,
        capture_pipe=None
    ):
        """
        Initialize Azure translator.
        
        Args:
            from_lang: Source language code (e.g., 'it-IT')
            to_lang: Target language code (e.g., 'en')
            voice_name: Azure voice name for synthesis
            push_stream: Azure push audio input stream
            tts: TTS engine instance (FastStreamingTTS)
            channel: Channel identifier ('A' or 'B')
            label: Label for logging
            turn_manager: Optional turn manager for priority control
            capture_pipe: Optional reference to capture pipe for keyboard control
        """
        self.label = label
        self.channel = channel
        self.turn_manager = turn_manager
        self.capture_pipe = capture_pipe
        self.tts = tts
        self.running = False
        self.phrase_count = 0
        
        # Create Azure Translation config
        ENDPOINT_URL = f"wss://{SPEECH_REGION}.stt.speech.microsoft.com/speech/universal/v2"
        self.config = speechsdk.translation.SpeechTranslationConfig(
            endpoint=ENDPOINT_URL, 
            subscription=SPEECH_KEY
        )
        self.config.speech_recognition_language = from_lang
        self.config.add_target_language(to_lang)
        self.config.voice_name = voice_name
        self.config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, 
            FINAL_SEG_MS
        )
        self.config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, 
            "1500"
        )
        
        # Create audio config from push stream
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        
        # Create translator
        self.translator = speechsdk.translation.TranslationRecognizer(
            translation_config=self.config, 
            audio_config=audio_config
        )
        
        # Create chunker for partial results
        self.chunker = BalancedChunker(label=label)
        
        log.info(f"{self.label} Azure Translator initialized: {from_lang} → {to_lang}")
    
    def start(self):
        """Start continuous translation recognition."""
        
        def on_recognizing(evt):
            """Handle partial recognition results (streaming)."""
            if USE_PARTIALS != "1":
                return
            
            try:
                if not self.running:
                    return
                
                # Check turn priority if manager available
                if self.turn_manager and self.turn_manager.get_state().value != self.channel:
                    return
                
                # Stop keyboard sound when translation starts
                if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                    if self.capture_pipe.keyboard_playing:
                        self.capture_pipe.keyboard_stop_event.set()
                        log.debug(f"{self.label} Keyboard stopped on partial recognition")
                
                # Process partial translations
                translations = evt.result.translations
                for lang, translated_text in translations.items():
                    if not translated_text:
                        continue
                    
                    # Use chunker to get speakable chunk
                    chunk = self.chunker.process_partial(translated_text)
                    if chunk:
                        log.info(f"{self.label} PARTIAL [{lang}] {chunk}")
                        self.tts.speak(chunk)
                        
            except Exception as e:
                log.error(f"{self.label} Error in recognizing callback: {e}")
        
        def on_recognized(evt):
            """Handle final recognition results."""
            try:
                if not self.running:
                    return
                
                if evt.result.reason != speechsdk.ResultReason.TranslatedSpeech:
                    return
                
                # Check turn priority if manager available
                if self.turn_manager and self.turn_manager.get_state().value != self.channel:
                    return
                
                # Get source text
                src_text = evt.result.text or ""
                if not src_text:
                    return
                
                self.phrase_count += 1
                log.info(f"{self.label} SOURCE {src_text}")
                
                # Stop keyboard sound when final translation arrives
                if self.capture_pipe and hasattr(self.capture_pipe, 'keyboard_stop_event'):
                    if self.capture_pipe.keyboard_playing:
                        self.capture_pipe.keyboard_stop_event.set()
                        log.debug(f"{self.label} Keyboard stopped on final recognition")
                
                # Process final translations
                for lang, translated_text in evt.result.translations.items():
                    if not translated_text:
                        continue
                    
                    if USE_PARTIALS == "1":
                        # If using partials, only speak remaining text
                        remaining = self.chunker.finalize(translated_text)
                        if remaining:
                            log.info(f"{self.label} FINAL [{lang}] {remaining}")
                            self.tts.speak(remaining)
                    else:
                        # If not using partials, speak entire final result
                        log.info(f"{self.label} FINAL [{lang}] {translated_text}")
                        self.tts.speak(translated_text)
                
                # Reset chunker for next phrase
                self.chunker.reset()
                
            except Exception as e:
                log.error(f"{self.label} Error in recognized callback: {e}")
        
        def on_canceled(evt):
            """Handle recognition cancellation/errors."""
            details = evt.cancellation_details
            if details.reason == speechsdk.CancellationReason.Error:
                log.error(f"{self.label} Recognition error: {details.error_details}")
        
        try:
            # Connect event handlers
            if USE_PARTIALS == "1":
                self.translator.recognizing.connect(on_recognizing)
            
            self.translator.recognized.connect(on_recognized)
            self.translator.canceled.connect(on_canceled)
            
            # Start continuous recognition
            self.running = True
            self.translator.start_continuous_recognition()
            log.info(f"{self.label} Azure Translation started (partials={'ON' if USE_PARTIALS=='1' else 'OFF'})")
            
        except Exception as e:
            log.error(f"{self.label} Failed to start translator: {e}")
            raise
    
    def stop(self):
        """Stop continuous translation recognition."""
        if not self.running:
            return
        
        try:
            self.running = False
            self.translator.stop_continuous_recognition()
            log.info(f"{self.label} Azure Translation stopped (phrases: {self.phrase_count})")
        except Exception as e:
            log.error(f"{self.label} Error stopping translator: {e}")
    
    def push_audio(self, audio_data: bytes):
        """
        Push audio data to recognizer.
        
        Note: For Azure, audio is pushed directly via PushAudioInputStream
        created externally, not through this method. This method exists
        for API compatibility with DeepL translator but is not used.
        
        Args:
            audio_data: PCM16 audio bytes
        """
        # Azure uses push stream directly from capture pipe
        # This method kept for interface compatibility
        pass
