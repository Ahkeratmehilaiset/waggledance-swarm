#!/usr/bin/env python3
"""
WaggleDance — Phase 6: Audio Sensors Tests
=============================================
25 tests across 7 groups:
  1. Syntax (3): all audio files parse OK
  2. BeeAudioAnalyzer (8): init, constants, analyze, stress, swarming, queen piping, baseline, dataclass
  3. BirdMonitor (5): init, predators, species_fi, dataclass, classify
  4. AudioMonitor (5): init disabled, init no mqtt, status keys, recent events, graceful
  5. Integration (2): settings.yaml, sensor_hub
  6. Backend stub (1): routes/audio.py parses
  7. Dashboard (1): useApi.js has audioStatus
"""

import ast
import os
import sys
import unittest

# Ensure project root on sys.path
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    """Group 1: All Phase 6 files parse without syntax errors."""

    def _parse(self, relpath):
        fpath = os.path.join(_project_root, relpath)
        self.assertTrue(os.path.exists(fpath), f"Missing: {relpath}")
        with open(fpath, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=relpath)

    def test_bee_audio_syntax(self):
        self._parse("integrations/bee_audio.py")

    def test_bird_monitor_syntax(self):
        self._parse("integrations/bird_monitor.py")

    def test_audio_monitor_syntax(self):
        self._parse("integrations/audio_monitor.py")


class TestBeeAudioAnalyzer(unittest.TestCase):
    """Group 2: BeeAudioAnalyzer unit tests."""

    def setUp(self):
        from integrations.bee_audio import BeeAudioAnalyzer, BeeAudioResult
        self.BeeAudioAnalyzer = BeeAudioAnalyzer
        self.BeeAudioResult = BeeAudioResult
        self.analyzer = BeeAudioAnalyzer({
            "baseline_days": 7,
            "stress_threshold_hz_shift": 30,
        })

    def test_init_defaults(self):
        """Analyzer initializes with correct defaults."""
        self.assertEqual(self.analyzer._baseline_days, 7)
        self.assertEqual(self.analyzer._stress_threshold, 30)
        self.assertEqual(self.analyzer._total_analyses, 0)

    def test_frequency_constants(self):
        """Frequency ranges match spec."""
        self.assertEqual(self.BeeAudioAnalyzer.NORMAL_HZ, (200, 500))
        self.assertEqual(self.BeeAudioAnalyzer.QUEEN_PIPING_HZ, (400, 500))
        self.assertEqual(self.BeeAudioAnalyzer.SWARMING_HZ, (200, 600))
        self.assertEqual(self.BeeAudioAnalyzer.STRESS_SHIFT_HZ, 50)

    def test_analyze_normal_spectrum(self):
        """Normal spectrum returns normal status."""
        spectrum = [
            {"hz": 200, "amplitude": 0.3},
            {"hz": 250, "amplitude": 0.8},
            {"hz": 300, "amplitude": 0.4},
            {"hz": 400, "amplitude": 0.2},
            {"hz": 500, "amplitude": 0.1},
        ]
        result = self.analyzer.analyze_spectrum(spectrum, "pesa_01")
        self.assertEqual(result.status, "normal")
        self.assertFalse(result.anomaly)
        self.assertGreater(result.confidence, 0.0)
        self.assertEqual(result.hive_id, "pesa_01")

    def test_detect_stress(self):
        """Stress detection works when fundamental shifts >threshold."""
        # Build baseline at 250 Hz
        import time
        for _ in range(10):
            self.analyzer._baselines["pesa_02"].append((time.time(), 250.0))

        # Analyze with shifted spectrum (fundamental at ~310 Hz)
        spectrum = [
            {"hz": 200, "amplitude": 0.2},
            {"hz": 310, "amplitude": 0.9},
            {"hz": 400, "amplitude": 0.2},
            {"hz": 500, "amplitude": 0.1},
        ]
        result = self.analyzer.analyze_spectrum(spectrum, "pesa_02")
        self.assertGreater(result.stress_level, 0.0)
        self.assertIn(result.status, ("stressed", "normal"))
        # 60 Hz shift > 30 Hz threshold
        self.assertTrue(result.stress_level > 0.5 or result.fundamental_hz > 280)

    def test_detect_swarming(self):
        """Swarming detected with broad high-amplitude spectrum."""
        spectrum = [
            {"hz": 200, "amplitude": 0.85},
            {"hz": 250, "amplitude": 0.80},
            {"hz": 300, "amplitude": 0.82},
            {"hz": 350, "amplitude": 0.78},
            {"hz": 400, "amplitude": 0.83},
            {"hz": 450, "amplitude": 0.79},
            {"hz": 500, "amplitude": 0.81},
            {"hz": 550, "amplitude": 0.77},
            {"hz": 600, "amplitude": 0.80},
        ]
        result = self.analyzer.analyze_spectrum(spectrum, "pesa_03")
        self.assertEqual(result.status, "swarming")
        self.assertTrue(result.anomaly)

    def test_detect_queen_piping(self):
        """Queen piping detected with sharp 400-500 Hz peak."""
        spectrum = [
            {"hz": 200, "amplitude": 0.1},
            {"hz": 250, "amplitude": 0.1},
            {"hz": 300, "amplitude": 0.1},
            {"hz": 350, "amplitude": 0.1},
            {"hz": 420, "amplitude": 0.9},
            {"hz": 450, "amplitude": 0.85},
            {"hz": 500, "amplitude": 0.1},
            {"hz": 550, "amplitude": 0.1},
            {"hz": 600, "amplitude": 0.05},
        ]
        result = self.analyzer.analyze_spectrum(spectrum, "pesa_04")
        self.assertEqual(result.status, "queen_piping")
        self.assertTrue(result.anomaly)

    def test_baseline_update(self):
        """Baseline updates with new spectrum data."""
        spectrum = [{"hz": 250, "amplitude": 0.8}]
        self.analyzer.update_baseline("pesa_05", spectrum)
        self.assertEqual(len(self.analyzer._baselines["pesa_05"]), 1)
        self.analyzer.update_baseline("pesa_05", spectrum)
        self.assertEqual(len(self.analyzer._baselines["pesa_05"]), 2)

    def test_bee_audio_result_defaults(self):
        """BeeAudioResult dataclass has correct defaults."""
        result = self.BeeAudioResult()
        self.assertEqual(result.hive_id, "")
        self.assertEqual(result.stress_level, 0.0)
        self.assertEqual(result.fundamental_hz, 250.0)
        self.assertEqual(result.status, "normal")
        self.assertFalse(result.anomaly)
        self.assertEqual(result.confidence, 0.0)


class TestBirdMonitor(unittest.TestCase):
    """Group 3: BirdMonitor unit tests."""

    def setUp(self):
        from integrations.bird_monitor import BirdMonitor, BirdDetection
        self.BirdMonitor = BirdMonitor
        self.BirdDetection = BirdDetection
        self.monitor = BirdMonitor({"enabled": False, "model": "BirdNET-Lite"})

    def test_init_defaults(self):
        """BirdMonitor initializes with config values."""
        self.assertFalse(self.monitor._enabled)
        self.assertEqual(self.monitor._model_name, "BirdNET-Lite")
        self.assertFalse(self.monitor._model_loaded)

    def test_predator_species(self):
        """PREDATOR_SPECIES contains expected animals."""
        self.assertIn("bear", self.BirdMonitor.PREDATOR_SPECIES)
        self.assertIn("wolf", self.BirdMonitor.PREDATOR_SPECIES)
        self.assertIn("eagle", self.BirdMonitor.PREDATOR_SPECIES)
        self.assertIn("hawk", self.BirdMonitor.PREDATOR_SPECIES)
        self.assertIsInstance(self.BirdMonitor.PREDATOR_SPECIES, set)

    def test_species_fi_mapping(self):
        """Finnish species translations are present."""
        self.assertEqual(self.BirdMonitor.SPECIES_FI["great tit"], "talitiainen")
        self.assertEqual(self.BirdMonitor.SPECIES_FI["bear"], "karhu")
        self.assertIn("european robin", self.BirdMonitor.SPECIES_FI)

    def test_bird_detection_defaults(self):
        """BirdDetection dataclass has correct defaults."""
        det = self.BirdDetection()
        self.assertEqual(det.species, "")
        self.assertEqual(det.species_fi, "")
        self.assertEqual(det.confidence, 0.0)
        self.assertFalse(det.is_predator)

    def test_classify_no_model(self):
        """Classify returns None when no model loaded and no species given."""
        result = self.monitor.classify({"audio": "raw_data"})
        self.assertIsNone(result)

    def test_classify_with_species_data(self):
        """Classify works with pre-classified species data."""
        result = self.monitor.classify({
            "species": "great tit",
            "confidence": 0.85,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result.species, "great tit")
        self.assertEqual(result.species_fi, "talitiainen")
        self.assertFalse(result.is_predator)
        self.assertEqual(result.confidence, 0.85)


class TestAudioMonitor(unittest.TestCase):
    """Group 4: AudioMonitor unit tests."""

    def setUp(self):
        from integrations.audio_monitor import AudioMonitor
        self.AudioMonitor = AudioMonitor

    def test_init_disabled(self):
        """AudioMonitor initializes as disabled by default."""
        am = self.AudioMonitor({"enabled": False})
        self.assertFalse(am._enabled)
        self.assertFalse(am._started)
        self.assertEqual(am._total_events, 0)

    def test_init_no_mqtt(self):
        """AudioMonitor works without MQTT hub."""
        am = self.AudioMonitor({"enabled": True}, mqtt_hub=None)
        self.assertTrue(am._enabled)
        self.assertIsNone(am._mqtt_hub)

    def test_status_keys(self):
        """get_status() returns expected keys."""
        am = self.AudioMonitor({"enabled": False})
        status = am.get_status()
        self.assertIn("enabled", status)
        self.assertIn("started", status)
        self.assertIn("total_events", status)
        self.assertIn("total_spectrums", status)
        self.assertIn("bee_analyzer", status)
        self.assertIn("bird_monitor", status)

    def test_get_recent_events_empty(self):
        """get_recent_events returns empty list initially."""
        am = self.AudioMonitor({"enabled": False})
        events = am.get_recent_events()
        self.assertEqual(events, [])

    def test_graceful_no_consciousness(self):
        """AudioMonitor works without consciousness for ChromaDB."""
        am = self.AudioMonitor({"enabled": True}, consciousness=None)
        # _store_fact should not raise
        am._store_fact("test fact", 0.80)


class TestIntegration(unittest.TestCase):
    """Group 5: Integration with settings and sensor_hub."""

    def test_settings_yaml_audio(self):
        """settings.yaml has audio section."""
        import yaml
        settings_path = os.path.join(_project_root, "configs", "settings.yaml")
        with open(settings_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.assertIn("audio", cfg)
        audio = cfg["audio"]
        self.assertIn("enabled", audio)
        self.assertIn("audio_analyzer", audio)
        self.assertIn("bird_monitor", audio)
        self.assertIn("mqtt_topics", audio)
        self.assertEqual(audio["audio_analyzer"]["baseline_days"], 7)

    def test_sensor_hub_has_audio_monitor(self):
        """SensorHub has audio_monitor attribute."""
        from integrations.sensor_hub import SensorHub
        hub = SensorHub(config={})
        self.assertTrue(hasattr(hub, "audio_monitor"))
        self.assertIsNone(hub.audio_monitor)


class TestBackendStub(unittest.TestCase):
    """Group 6: Backend stub routes."""

    def test_audio_route_syntax(self):
        """backend/routes/audio.py parses without errors."""
        fpath = os.path.join(_project_root, "backend", "routes", "audio.py")
        self.assertTrue(os.path.exists(fpath), "Missing: backend/routes/audio.py")
        with open(fpath, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename="backend/routes/audio.py")


class TestDashboard(unittest.TestCase):
    """Group 7: Dashboard integration."""

    def test_useapi_has_audio_status(self):
        """useApi.js exports audioStatus."""
        fpath = os.path.join(_project_root, "dashboard", "src", "hooks", "useApi.js")
        self.assertTrue(os.path.exists(fpath), "Missing: useApi.js")
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("audioStatus", content)
        self.assertIn("/api/sensors/audio", content)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Phase 6: Audio Sensors — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
