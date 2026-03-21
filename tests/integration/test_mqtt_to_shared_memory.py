"""Integration tests for MQTT sensor ingest — store callback, anomaly callback, multi-hive, filtering."""

import unittest

from core.mqtt_sensor_ingest import MQTTSensorIngest, SensorReading


class TestFullPathStoreCallback(unittest.TestCase):
    def test_full_path_store_callback(self):
        """Valid message fires on_store callback with correct SensorReading."""
        stored = []
        ingest = MQTTSensorIngest(on_store=lambda r: stored.append(r))
        ingest.on_message("hive/alpha/temperature", "34.5")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].hive_id, "alpha")
        self.assertAlmostEqual(stored[0].temperature_c, 34.5)
        self.assertTrue(stored[0].valid)

    def test_store_callback_not_called_for_invalid(self):
        """Invalid payload does not fire on_store."""
        stored = []
        ingest = MQTTSensorIngest(on_store=lambda r: stored.append(r))
        ingest.on_message("hive/alpha/temperature", "garbage")
        self.assertEqual(len(stored), 0)

    def test_store_callback_not_called_for_out_of_range(self):
        """Out-of-range temperature does not fire on_store."""
        stored = []
        ingest = MQTTSensorIngest(on_store=lambda r: stored.append(r))
        ingest.on_message("hive/alpha/temperature", "-50.0")
        self.assertEqual(len(stored), 0)


class TestAnomalyCallbackTriggered(unittest.TestCase):
    def test_anomaly_callback_triggered(self):
        """Cold temperature triggers on_anomaly callback with description."""
        anomalies = []
        ingest = MQTTSensorIngest(on_anomaly=lambda r, desc: anomalies.append((r, desc)))
        ingest.on_message("hive/beta/temperature", "5.0")
        self.assertEqual(len(anomalies), 1)
        reading, desc = anomalies[0]
        self.assertEqual(reading.hive_id, "beta")
        self.assertIn("Cold anomaly", desc)

    def test_heat_anomaly_callback(self):
        """Heat anomaly fires on_anomaly callback."""
        anomalies = []
        ingest = MQTTSensorIngest(on_anomaly=lambda r, desc: anomalies.append((r, desc)))
        ingest.on_message("hive/gamma/temperature", "50.0")
        self.assertEqual(len(anomalies), 1)
        _, desc = anomalies[0]
        self.assertIn("Heat anomaly", desc)

    def test_no_anomaly_callback_for_normal(self):
        """Normal temperature does not trigger on_anomaly."""
        anomalies = []
        ingest = MQTTSensorIngest(on_anomaly=lambda r, desc: anomalies.append((r, desc)))
        ingest.on_message("hive/gamma/temperature", "30.0")
        self.assertEqual(len(anomalies), 0)

    def test_both_callbacks_fire_on_anomaly(self):
        """Both on_store and on_anomaly fire for a valid anomalous reading."""
        stored = []
        anomalies = []
        ingest = MQTTSensorIngest(
            on_store=lambda r: stored.append(r),
            on_anomaly=lambda r, desc: anomalies.append((r, desc)),
        )
        ingest.on_message("hive/delta/temperature", "3.0")
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(anomalies), 1)


class TestMultipleHives(unittest.TestCase):
    def test_multiple_hives(self):
        """Readings from multiple hives are all stored."""
        ingest = MQTTSensorIngest()
        ingest.on_message("hive/alpha/temperature", "30.0")
        ingest.on_message("hive/beta/temperature", "32.0")
        ingest.on_message("hive/gamma/temperature", "28.0")
        self.assertEqual(ingest.reading_count, 3)

    def test_multiple_readings_per_hive(self):
        """Multiple readings from the same hive accumulate."""
        ingest = MQTTSensorIngest()
        ingest.on_message("hive/alpha/temperature", "30.0")
        ingest.on_message("hive/alpha/temperature", "31.0")
        ingest.on_message("hive/alpha/temperature", "32.0")
        self.assertEqual(ingest.reading_count, 3)
        readings = ingest.recent_readings(hive_id="alpha")
        self.assertEqual(len(readings), 3)


class TestRecentReadingsFilter(unittest.TestCase):
    def test_recent_readings_filter(self):
        """recent_readings filters by hive_id."""
        ingest = MQTTSensorIngest()
        ingest.on_message("hive/alpha/temperature", "30.0")
        ingest.on_message("hive/beta/temperature", "32.0")
        ingest.on_message("hive/alpha/temperature", "31.0")

        alpha = ingest.recent_readings(hive_id="alpha")
        self.assertEqual(len(alpha), 2)
        for r in alpha:
            self.assertEqual(r.hive_id, "alpha")

        beta = ingest.recent_readings(hive_id="beta")
        self.assertEqual(len(beta), 1)
        self.assertEqual(beta[0].hive_id, "beta")

    def test_recent_readings_limit(self):
        """recent_readings respects the limit parameter."""
        ingest = MQTTSensorIngest()
        for i in range(20):
            ingest.on_message("hive/alpha/temperature", str(20.0 + i * 0.1))
        recent = ingest.recent_readings(limit=5)
        self.assertEqual(len(recent), 5)
        # Should be the last 5 readings
        self.assertAlmostEqual(recent[0].temperature_c, 21.5)

    def test_recent_readings_all_hives(self):
        """recent_readings without hive_id returns all hives."""
        ingest = MQTTSensorIngest()
        ingest.on_message("hive/alpha/temperature", "30.0")
        ingest.on_message("hive/beta/temperature", "32.0")
        all_readings = ingest.recent_readings()
        self.assertEqual(len(all_readings), 2)

    def test_recent_readings_empty(self):
        """recent_readings returns empty list for unknown hive."""
        ingest = MQTTSensorIngest()
        ingest.on_message("hive/alpha/temperature", "30.0")
        result = ingest.recent_readings(hive_id="nonexistent")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
