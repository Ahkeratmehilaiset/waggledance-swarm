"""
WaggleDance Voice Interface — Phase 7
Full voice pipeline: Mic -> STT -> HiveMind -> TTS -> Speaker

Pipeline: Mic -> Whisper(STT,fi) -> HiveMind.chat() -> Piper(TTS,fi) -> Speaker
Dashboard integration via WebSocket + MediaRecorder.
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("voice_interface")


@dataclass
class VoiceResponse:
    """Complete voice interaction result."""
    input_text: str = ""
    output_text: str = ""
    audio_bytes: bytes = b""
    stt_latency_ms: float = 0.0
    chat_latency_ms: float = 0.0
    tts_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    language: str = "fi"
    wake_word_detected: bool = False


class VoiceInterface:
    """Orchestrates the full voice pipeline.

    Components:
    - WhisperSTT: speech-to-text (Finnish)
    - HiveMind.chat(): AI response
    - PiperTTS: text-to-speech (Finnish)
    - WebSocket: audio streaming to/from dashboard

    Graceful degradation: if any component unavailable,
    the remaining components still work (text fallback).
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        voice_cfg = cfg.get("voice", {})

        self._enabled = voice_cfg.get("enabled", False)
        self._stt = None
        self._tts = None
        self._chat_fn: Optional[Callable] = None
        self._ws_callback: Optional[Callable] = None
        self._ready = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self, chat_fn: Callable = None,
                         ws_callback: Callable = None) -> bool:
        """Initialize voice components.

        Args:
            chat_fn: async function(message: str) -> str (HiveMind.chat)
            ws_callback: async function(event, data) for WebSocket
        """
        if not self._enabled:
            log.info("Voice interface disabled in config")
            return False

        self._chat_fn = chat_fn
        self._ws_callback = ws_callback

        # Initialize STT
        try:
            from integrations.whisper_stt import WhisperSTT
            stt_cfg = {}
            self._stt = WhisperSTT(stt_cfg)
            if await self._stt.initialize():
                log.info("Voice STT ready (Whisper)")
            else:
                log.warning("Voice STT init failed, text-only mode")
                self._stt = None
        except ImportError:
            log.warning("whisper_stt not available")
            self._stt = None

        # Initialize TTS
        try:
            from integrations.piper_tts import PiperTTS
            tts_cfg = {}
            self._tts = PiperTTS(tts_cfg)
            if await self._tts.initialize():
                log.info("Voice TTS ready (Piper)")
            else:
                log.warning("Voice TTS init failed, text-only response")
                self._tts = None
        except ImportError:
            log.warning("piper_tts not available")
            self._tts = None

        self._ready = bool(self._stt or self._tts)
        return self._ready

    async def process_audio(self, audio_bytes: bytes,
                            sample_rate: int = 16000) -> VoiceResponse:
        """Full voice pipeline: audio -> text -> AI -> audio.

        Args:
            audio_bytes: Raw PCM audio (16-bit, mono)
            sample_rate: Audio sample rate

        Returns:
            VoiceResponse with text and audio output
        """
        t0 = time.monotonic()
        response = VoiceResponse()

        # Step 1: STT — speech to text
        if self._stt:
            stt_result = await self._stt.transcribe(audio_bytes, sample_rate)
            response.input_text = stt_result.text
            response.stt_latency_ms = stt_result.latency_ms
            response.language = stt_result.language
            response.wake_word_detected = stt_result.wake_word_detected

            if self._ws_callback:
                await self._ws_callback("voice_stt", {
                    "text": stt_result.text,
                    "confidence": stt_result.confidence,
                    "language": stt_result.language,
                })
        else:
            log.warning("STT not available, cannot process audio")
            return response

        if not response.input_text:
            return response

        # Step 2: Chat — get AI response
        t1 = time.monotonic()
        if self._chat_fn:
            try:
                response.output_text = await self._chat_fn(
                    response.input_text)
            except Exception as e:
                log.error(f"Chat error in voice pipeline: {e}")
                response.output_text = ("Anteeksi, en pystynyt vastaamaan. "
                                        "Yritä uudelleen.")
        response.chat_latency_ms = (time.monotonic() - t1) * 1000

        if self._ws_callback:
            await self._ws_callback("voice_chat", {
                "input": response.input_text,
                "output": response.output_text[:200],
            })

        # Step 3: TTS — text to speech
        if self._tts and response.output_text:
            tts_result = await self._tts.synthesize(response.output_text)
            response.audio_bytes = tts_result.audio_bytes
            response.tts_latency_ms = tts_result.latency_ms

            if self._ws_callback and tts_result.audio_bytes:
                # Send audio as base64 for WebSocket
                audio_b64 = base64.b64encode(
                    tts_result.audio_bytes).decode("ascii")
                await self._ws_callback("voice_tts", {
                    "audio_base64": audio_b64,
                    "sample_rate": tts_result.sample_rate,
                    "duration_s": tts_result.duration_s,
                })

        response.total_latency_ms = (time.monotonic() - t0) * 1000

        log.info(
            f"Voice pipeline: '{response.input_text[:40]}' -> "
            f"'{response.output_text[:40]}' "
            f"(STT={response.stt_latency_ms:.0f}ms, "
            f"Chat={response.chat_latency_ms:.0f}ms, "
            f"TTS={response.tts_latency_ms:.0f}ms, "
            f"Total={response.total_latency_ms:.0f}ms)")

        return response

    async def process_text(self, text: str) -> VoiceResponse:
        """Text-only pipeline (bypass STT): text -> AI -> audio.

        Useful for dashboard text input with audio response.
        """
        t0 = time.monotonic()
        response = VoiceResponse(input_text=text)

        # Chat
        if self._chat_fn:
            try:
                response.output_text = await self._chat_fn(text)
            except Exception as e:
                log.error(f"Chat error: {e}")
                response.output_text = "Anteeksi, virhe tapahtui."
        response.chat_latency_ms = (time.monotonic() - t0) * 1000

        # TTS
        if self._tts and response.output_text:
            tts_result = await self._tts.synthesize(response.output_text)
            response.audio_bytes = tts_result.audio_bytes
            response.tts_latency_ms = tts_result.latency_ms

        response.total_latency_ms = (time.monotonic() - t0) * 1000
        return response

    def status(self) -> dict:
        """Return component status for dashboard."""
        return {
            "enabled": self._enabled,
            "ready": self._ready,
            "stt_available": self._stt is not None and self._stt.ready,
            "tts_available": self._tts is not None and self._tts.ready,
            "chat_connected": self._chat_fn is not None,
        }
