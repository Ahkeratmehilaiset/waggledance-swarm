#!/usr/bin/env python3
"""
Phase 7: Voice Interface — Tests
~20 tests covering syntax, WhisperSTT, PiperTTS, VoiceInterface, dataclasses.
"""
import ast
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Project root on sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _run(coro):
    """Run async coroutine in sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════
# Group 1: Syntax — all 3 voice files parse OK
# ═══════════════════════════════════════════════════════════════

class TestVoiceSyntax(unittest.TestCase):
    """All voice integration files must parse without errors."""

    def test_whisper_stt_syntax(self):
        path = _root / "integrations" / "whisper_stt.py"
        self.assertTrue(path.exists(), f"Missing: {path}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)  # raises SyntaxError on failure

    def test_piper_tts_syntax(self):
        path = _root / "integrations" / "piper_tts.py"
        self.assertTrue(path.exists(), f"Missing: {path}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)

    def test_voice_interface_syntax(self):
        path = _root / "integrations" / "voice_interface.py"
        self.assertTrue(path.exists(), f"Missing: {path}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)


# ═══════════════════════════════════════════════════════════════
# Group 2: WhisperSTT
# ═══════════════════════════════════════════════════════════════

class TestWhisperSTT(unittest.TestCase):
    """WhisperSTT unit tests (no actual Whisper model needed)."""

    def test_init_defaults(self):
        from integrations.whisper_stt import WhisperSTT
        stt = WhisperSTT()
        self.assertFalse(stt.ready)
        self.assertEqual(stt._model_name, "small")
        self.assertEqual(stt._device, "cpu")
        self.assertEqual(stt._language, "fi")

    def test_init_custom_config(self):
        from integrations.whisper_stt import WhisperSTT
        stt = WhisperSTT({"model": "tiny", "device": "cuda", "language": "en"})
        self.assertEqual(stt._model_name, "tiny")
        self.assertEqual(stt._device, "cuda")
        self.assertEqual(stt._language, "en")

    def test_wake_word_detect(self):
        from integrations.whisper_stt import WhisperSTT
        stt = WhisperSTT()
        self.assertTrue(stt._check_wake_word("Hei WaggleDance, kerro säästä"))
        self.assertTrue(stt._check_wake_word("hei waggledance"))
        self.assertFalse(stt._check_wake_word("Hei Google"))
        self.assertFalse(stt._check_wake_word(""))

    def test_wake_word_strip(self):
        from integrations.whisper_stt import WhisperSTT
        stt = WhisperSTT()
        self.assertEqual(stt._strip_wake_word("Hei WaggleDance, kerro säästä"),
                         "kerro säästä")
        self.assertEqual(stt._strip_wake_word("hei waggledance kerro jotain"),
                         "kerro jotain")
        # No wake word — returns as-is
        self.assertEqual(stt._strip_wake_word("mikä on sää"), "mikä on sää")

    def test_wake_phrase_constant(self):
        from integrations.whisper_stt import WhisperSTT
        self.assertEqual(WhisperSTT.WAKE_PHRASE, "hei waggledance")


# ═══════════════════════════════════════════════════════════════
# Group 3: PiperTTS
# ═══════════════════════════════════════════════════════════════

class TestPiperTTS(unittest.TestCase):
    """PiperTTS unit tests (no actual Piper model needed)."""

    def test_init_defaults(self):
        from integrations.piper_tts import PiperTTS
        tts = PiperTTS()
        self.assertFalse(tts.ready)
        self.assertEqual(tts._voice, "fi_FI-harri-medium")
        self.assertEqual(tts._sample_rate, 22050)

    def test_default_voice_constant(self):
        from integrations.piper_tts import PiperTTS
        self.assertEqual(PiperTTS.DEFAULT_VOICE, "fi_FI-harri-medium")

    def test_synthesis_result_fields(self):
        from integrations.piper_tts import SynthesisResult
        r = SynthesisResult()
        self.assertEqual(r.audio_bytes, b"")
        self.assertEqual(r.sample_rate, 22050)
        self.assertEqual(r.duration_s, 0.0)
        self.assertEqual(r.latency_ms, 0.0)
        self.assertEqual(r.text_length, 0)


# ═══════════════════════════════════════════════════════════════
# Group 4: VoiceInterface
# ═══════════════════════════════════════════════════════════════

class TestVoiceInterface(unittest.TestCase):
    """VoiceInterface orchestrator tests."""

    def test_init_disabled(self):
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface({"voice": {"enabled": False}})
        self.assertFalse(vi.enabled)
        self.assertFalse(vi.ready)

    def test_init_no_config(self):
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface()
        self.assertFalse(vi.enabled)

    def test_status_keys(self):
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface()
        s = vi.status()
        expected_keys = {"enabled", "ready", "stt_available", "tts_available",
                         "chat_connected"}
        self.assertEqual(set(s.keys()), expected_keys)

    def test_graceful_no_stt(self):
        """process_audio without STT returns empty response."""
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface({"voice": {"enabled": True}})
        vi._ready = True
        vi._stt = None
        result = _run(vi.process_audio(b"\x00" * 100))
        self.assertEqual(result.input_text, "")

    def test_graceful_no_tts(self):
        """process_text without TTS returns text but no audio."""
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface({"voice": {"enabled": True}})
        vi._ready = True
        vi._tts = None
        vi._chat_fn = AsyncMock(return_value="Testiä")
        result = _run(vi.process_text("hei"))
        self.assertEqual(result.output_text, "Testiä")
        self.assertEqual(result.audio_bytes, b"")

    def test_chat_fn_none_fallback(self):
        """process_text with chat_fn=None returns empty output."""
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface({"voice": {"enabled": True}})
        vi._ready = True
        vi._chat_fn = None
        result = _run(vi.process_text("hei"))
        self.assertEqual(result.output_text, "")


# ═══════════════════════════════════════════════════════════════
# Group 5: Dataclasses
# ═══════════════════════════════════════════════════════════════

class TestVoiceDataclasses(unittest.TestCase):
    """VoiceResponse and TranscriptionResult dataclass defaults."""

    def test_voice_response_defaults(self):
        from integrations.voice_interface import VoiceResponse
        r = VoiceResponse()
        self.assertEqual(r.input_text, "")
        self.assertEqual(r.output_text, "")
        self.assertEqual(r.audio_bytes, b"")
        self.assertEqual(r.language, "fi")
        self.assertFalse(r.wake_word_detected)

    def test_transcription_result_defaults(self):
        from integrations.whisper_stt import TranscriptionResult
        r = TranscriptionResult()
        self.assertEqual(r.text, "")
        self.assertEqual(r.language, "fi")
        self.assertEqual(r.confidence, 0.0)
        self.assertFalse(r.wake_word_detected)


# ═══════════════════════════════════════════════════════════════
# Group 6: Integration
# ═══════════════════════════════════════════════════════════════

class TestVoiceIntegration(unittest.TestCase):
    """Integration-level checks."""

    def test_settings_yaml_voice_section(self):
        """settings.yaml has a voice section."""
        import yaml
        path = _root / "configs" / "settings.yaml"
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        voice = cfg.get("voice", {})
        self.assertIn("enabled", voice)
        self.assertFalse(voice["enabled"], "voice should be disabled by default")

    def test_initialize_disabled_returns_false(self):
        """VoiceInterface.initialize() returns False when disabled."""
        from integrations.voice_interface import VoiceInterface
        vi = VoiceInterface({"voice": {"enabled": False}})
        result = _run(vi.initialize())
        self.assertFalse(result)

    def test_backend_voice_route_parseable(self):
        """_archive/backend-legacy/routes/voice.py parses without error."""
        path = _root / "_archive" / "backend-legacy" / "routes" / "voice.py"
        self.assertTrue(path.exists(), f"Missing: {path}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
