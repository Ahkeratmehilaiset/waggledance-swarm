"""
WaggleDance Piper TTS — Phase 7
Text-to-speech using Piper (local, CPU-based).

Pipeline: Text -> Piper TTS -> WAV audio -> WebSocket stream
Voice: fi_FI-harri-medium (Finnish male voice)
"""

import asyncio
import io
import logging
import struct
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("piper_tts")


@dataclass
class SynthesisResult:
    """Result of text-to-speech synthesis."""
    audio_bytes: bytes = b""
    sample_rate: int = 22050
    duration_s: float = 0.0
    latency_ms: float = 0.0
    text_length: int = 0


class PiperTTS:
    """Local Piper-based text-to-speech for Finnish.

    Uses fi_FI-harri-medium voice model. Generates WAV audio
    that can be streamed to the dashboard via WebSocket.
    """

    DEFAULT_VOICE = "fi_FI-harri-medium"
    SAMPLE_RATE = 22050

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._voice = cfg.get("voice", self.DEFAULT_VOICE)
        self._data_dir = Path(cfg.get("data_dir", "data/piper_models"))
        self._speed = cfg.get("speed", 1.0)
        self._sample_rate = cfg.get("sample_rate", self.SAMPLE_RATE)

        self._engine = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> bool:
        """Load Piper TTS engine (blocking, run once at startup)."""
        try:
            from piper import PiperVoice

            model_path = self._data_dir / f"{self._voice}.onnx"
            config_path = self._data_dir / f"{self._voice}.onnx.json"

            if not model_path.exists():
                log.warning(
                    f"Piper voice model not found: {model_path}. "
                    f"Download with: piper --download-dir {self._data_dir} "
                    f"--model {self._voice}")
                return False

            log.info(f"Loading Piper voice: {self._voice}")
            t0 = time.monotonic()

            loop = asyncio.get_event_loop()
            self._engine = await loop.run_in_executor(
                None, PiperVoice.load, str(model_path), str(config_path))

            elapsed = time.monotonic() - t0
            log.info(f"Piper voice loaded in {elapsed:.1f}s")
            self._ready = True
            return True

        except ImportError:
            log.warning("Piper not installed. "
                        "Install with: pip install piper-tts")
            return False
        except Exception as e:
            log.error(f"Piper init failed: {e}")
            return False

    async def synthesize(self, text: str) -> SynthesisResult:
        """Convert text to WAV audio bytes.

        Args:
            text: Finnish text to speak

        Returns:
            SynthesisResult with WAV audio bytes
        """
        if not self._engine or not text.strip():
            return SynthesisResult()

        t0 = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            audio_bytes = await loop.run_in_executor(
                None, self._synthesize_sync, text)

            latency = (time.monotonic() - t0) * 1000
            duration = len(audio_bytes) / (self._sample_rate * 2)  # 16-bit

            log.info(f"TTS: '{text[:40]}...' -> {duration:.1f}s audio "
                     f"({latency:.0f}ms)")

            return SynthesisResult(
                audio_bytes=audio_bytes,
                sample_rate=self._sample_rate,
                duration_s=duration,
                latency_ms=latency,
                text_length=len(text),
            )
        except Exception as e:
            log.error(f"Synthesis error: {e}")
            return SynthesisResult()

    def _synthesize_sync(self, text: str) -> bytes:
        """Synchronous Piper synthesis (for thread pool)."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            self._engine.synthesize(text, wf)
        return buf.getvalue()

    async def synthesize_to_file(self, text: str,
                                 output_path: str) -> SynthesisResult:
        """Synthesize text and save to WAV file."""
        result = await self.synthesize(text)
        if result.audio_bytes:
            try:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(result.audio_bytes)
            except Exception as e:
                log.error(f"Save audio error: {e}")
        return result
