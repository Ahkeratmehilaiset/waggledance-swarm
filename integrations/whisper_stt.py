"""
WaggleDance Whisper STT — Phase 7
Speech-to-text using OpenAI Whisper (local, CPU-based).

Pipeline: Mic audio -> VAD silence detection -> Whisper transcription -> text
Model: whisper-small (Finnish, WER ~15%)
Wake word: "Hei WaggleDance" via openWakeWord
"""

import asyncio
import io
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("whisper_stt")


@dataclass
class TranscriptionResult:
    """Result of a speech-to-text transcription."""
    text: str = ""
    language: str = "fi"
    confidence: float = 0.0
    duration_s: float = 0.0
    latency_ms: float = 0.0
    wake_word_detected: bool = False


class WhisperSTT:
    """Local Whisper-based speech-to-text for Finnish.

    Uses whisper-small model on CPU. Supports wake word detection
    and VAD (Voice Activity Detection) silence cutoff.
    """

    WAKE_PHRASE = "hei waggledance"
    VAD_SILENCE_S = 1.5  # seconds of silence to end recording
    SAMPLE_RATE = 16000
    CHANNELS = 1

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._model_name = cfg.get("model", "small")
        self._device = cfg.get("device", "cpu")
        self._language = cfg.get("language", "fi")
        self._wake_word_enabled = cfg.get("wake_word", True)
        self._vad_silence_s = cfg.get("vad_silence_s", self.VAD_SILENCE_S)

        self._model = None
        self._wake_model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> bool:
        """Load Whisper model (blocking, run once at startup)."""
        try:
            import whisper
            log.info(f"Loading Whisper model: {self._model_name} "
                     f"(device={self._device})")
            t0 = time.monotonic()
            self._model = whisper.load_model(
                self._model_name, device=self._device)
            elapsed = time.monotonic() - t0
            log.info(f"Whisper model loaded in {elapsed:.1f}s")

            # Optional wake word model
            if self._wake_word_enabled:
                try:
                    from openwakeword.model import Model as WakeModel
                    self._wake_model = WakeModel(
                        wakeword_models=["hey_waggledance"],
                        inference_framework="onnx")
                    log.info("Wake word model loaded (openWakeWord)")
                except ImportError:
                    log.info("openWakeWord not available, "
                             "wake word detection disabled")
                except Exception as e:
                    log.warning(f"Wake word init failed: {e}")

            self._ready = True
            return True

        except ImportError:
            log.warning("Whisper not installed. "
                        "Install with: pip install openai-whisper")
            return False
        except Exception as e:
            log.error(f"Whisper init failed: {e}")
            return False

    async def transcribe(self, audio_bytes: bytes,
                         sample_rate: int = 16000) -> TranscriptionResult:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw PCM audio (16-bit, mono, 16kHz)
            sample_rate: Sample rate (default 16000)

        Returns:
            TranscriptionResult with text, language, confidence
        """
        if not self._model:
            return TranscriptionResult(text="", confidence=0.0)

        t0 = time.monotonic()
        try:
            # Convert bytes to numpy array
            import numpy as np
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(
                np.float32) / 32768.0

            # Run transcription in thread pool (CPU-bound)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._transcribe_sync, audio_np)

            latency = (time.monotonic() - t0) * 1000
            duration = len(audio_np) / sample_rate

            text = result.get("text", "").strip()
            language = result.get("language", self._language)

            # Check for wake word
            wake_detected = False
            if self._wake_word_enabled and text:
                wake_detected = self._check_wake_word(text)
                if wake_detected:
                    # Remove wake phrase from text
                    text = self._strip_wake_word(text)

            # Estimate confidence from Whisper's avg_logprob
            segments = result.get("segments", [])
            if segments:
                avg_logprob = sum(
                    s.get("avg_logprob", -1.0) for s in segments
                ) / len(segments)
                # Map logprob to 0-1 confidence
                confidence = max(0.0, min(1.0, 1.0 + avg_logprob / 2.0))
            else:
                confidence = 0.0

            log.info(f"STT: '{text[:60]}' "
                     f"(lang={language}, conf={confidence:.2f}, "
                     f"{latency:.0f}ms, {duration:.1f}s audio)")

            return TranscriptionResult(
                text=text,
                language=language,
                confidence=confidence,
                duration_s=duration,
                latency_ms=latency,
                wake_word_detected=wake_detected,
            )

        except Exception as e:
            log.error(f"Transcription error: {e}")
            return TranscriptionResult(text="", confidence=0.0)

    def _transcribe_sync(self, audio_np) -> dict:
        """Synchronous Whisper transcription (for thread pool)."""
        return self._model.transcribe(
            audio_np,
            language=self._language,
            task="transcribe",
            fp16=False,  # CPU mode
        )

    def _check_wake_word(self, text: str) -> bool:
        """Check if text starts with wake phrase."""
        normalized = text.lower().strip()
        return normalized.startswith(self.WAKE_PHRASE)

    def _strip_wake_word(self, text: str) -> str:
        """Remove wake phrase prefix from text."""
        normalized = text.lower().strip()
        if normalized.startswith(self.WAKE_PHRASE):
            text = text[len(self.WAKE_PHRASE):].strip()
            # Remove punctuation after wake phrase
            if text and text[0] in ".,!?:;":
                text = text[1:].strip()
        return text

    async def transcribe_file(self, path: str) -> TranscriptionResult:
        """Transcribe audio from a WAV file."""
        try:
            with wave.open(path, "rb") as wf:
                audio_bytes = wf.readframes(wf.getnframes())
                sample_rate = wf.getframerate()
            return await self.transcribe(audio_bytes, sample_rate)
        except Exception as e:
            log.error(f"File transcription error: {e}")
            return TranscriptionResult(text="", confidence=0.0)
