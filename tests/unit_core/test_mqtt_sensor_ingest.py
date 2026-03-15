"""Tests for core.mqtt_sensor_ingest — MQTT hive temperature sensor ingest (Phase 6)."""

import time
import unittest

from core.mqtt_sensor_ingest import MQTTSensorIngest, SensorReading, TOPIC_PATTERN


class TestParseTopic(unittest.TestCase):
    def test_parse_topic_valid(self):
        ingest = MQTTSensorIngest()
        self.assertEqual(ingest.parse_topic("hive/alpha/temperature"), "alpha")

    def test_parse_topic_valid_numeric(self):
        ingest = MQTTSensorIngest()
        self.assertEqual(ingest.parse_topic("hive/42/temperature"), "42")

    def test_parse_topic_invalid_no_match(self):
        ingest = MQTTSensorIngest()
        self.assertIsNone(ingest.parse_topic("sensor/alpha/temperature"))

    def test_parse_topic_invalid_extra_segment(self):
        ingest = MQTTSensorIngest()
        self.assertIsNone(ingest.parse_topic("hive/alpha/beta/temperature"))

    def test_parse_topic_invalid_wrong_suffix(self):
        ingest = MQTTSensorIngest()
        self.assertIsNone(ingest.parse_topic("hive/alpha/humidity"))


class TestParsePayload(unittest.TestCase):
    def test_parse_payload_float(self):
        ingest = MQTTSensorIngest()
        self.assertAlmostEqual(ingest.parse_payload("34.5"), 34.5)

    def test_parse_payload_integer_string(self):
        ingest = MQTTSensorIngest()
        self.assertAlmostEqual(ingest.parse_payload("20"), 20.0)

    def test_parse_payload_bytes(self):
        ingest = MQTTSensorIngest()
        self.assertAlmostEqual(ingest.parse_payload(b"25.3"), 25.3)

    def test_parse_payload_with_whitespace(self):
        ingest = MQTTSensorIngest()
        self.assertAlmostEqual(ingest.parse_payload("  22.1  "), 22.1)

    def test_parse_payload_invalid(self):
        ingest = MQTTSensorIngest()
        self.assertIsNone(ingest.parse_payload("not_a_number"))

    def test_parse_payload_invalid_bytes(self):
        ingest = MQTTSensorIngest()
        self.assertIsNone(ingest.parse_payload(b"\xff\xfe"))


class TestValidateRange(unittest.TestCase):
    def test_validate_range_normal(self):
        ingest = MQTTSensorIngest()
        valid, error = ingest.validate_range(25.0)
        self.assertTrue(valid)
        self.assertEqual(error, "")

    def test_validate_range_too_low(self):
        ingest = MQTTSensorIngest()
        valid, error = ingest.validate_range(-50.0)
        self.assertFalse(valid)
        self.assertIn("Below minimum", error)

    def test_validate_range_too_high(self):
        ingest = MQTTSensorIngest()
        valid, error = ingest.validate_range(90.0)
        self.assertFalse(valid)
        self.assertIn("Above maximum", error)

    def test_validate_range_at_min_boundary(self):
        ingest = MQTTSensorIngest()
        valid, _ = ingest.validate_range(-40.0)
        self.assertTrue(valid)

    def test_validate_range_at_max_boundary(self):
        ingest = MQTTSensorIngest()
        valid, _ = ingest.validate_range(80.0)
        self.assertTrue(valid)


class TestCheckAnomaly(unittest.TestCase):
    def test_anomaly_cold(self):
        ingest = MQTTSensorIngest()
        reading = SensorReading(hive_id="h1", temperature_c=5.0)
        result = ingest.check_anomaly(reading)
        self.assertIsNotNone(result)
        self.assertIn("Cold anomaly", result)

    def test_anomaly_heat(self):
        ingest = MQTTSensorIngest()
        reading = SensorReading(hive_id="h1", temperature_c=50.0)
        result = ingest.check_anomaly(reading)
        self.assertIsNotNone(result)
        self.assertIn("Heat anomaly", result)

    def test_anomaly_normal(self):
        ingest = MQTTSensorIngest()
        reading = SensorReading(hive_id="h1", temperature_c=30.0)
        result = ingest.check_anomaly(reading)
        self.assertIsNone(result)

    def test_anomaly_at_low_threshold(self):
        ingest = MQTTSensorIngest()
        reading = SensorReading(hive_id="h1", temperature_c=10.0)
        result = ingest.check_anomaly(reading)
        self.assertIsNone(result)

    def test_anomaly_at_high_threshold(self):
        ingest = MQTTSensorIngest()
        reading = SensorReading(hive_id="h1", temperature_c=45.0)
        result = ingest.check_anomaly(reading)
        self.assertIsNone(result)


class TestOnMessage(unittest.TestCase):
    def test_on_message_valid(self):
        ingest = MQTTSensorIngest()
        reading = ingest.on_message("hive/alpha/temperature", "35.2")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.hive_id, "alpha")
        self.assertAlmostEqual(reading.temperature_c, 35.2)
        self.assertTrue(reading.valid)
        self.assertEqual(reading.error, "")
        self.assertEqual(ingest.reading_count, 1)

    def test_on_message_invalid_topic(self):
        ingest = MQTTSensorIngest()
        reading = ingest.on_message("sensor/alpha/temperature", "35.2")
        self.assertIsNone(reading)
        self.assertEqual(ingest.reading_count, 0)

    def test_on_message_invalid_payload(self):
        ingest = MQTTSensorIngest()
        reading = ingest.on_message("hive/alpha/temperature", "garbage")
        self.assertIsNotNone(reading)
        self.assertFalse(reading.valid)
        self.assertEqual(reading.error, "Invalid payload")
        self.assertEqual(ingest.reading_count, 0)

    def test_on_message_out_of_range(self):
        ingest = MQTTSensorIngest()
        reading = ingest.on_message("hive/alpha/temperature", "100.0")
        self.assertIsNotNone(reading)
        self.assertFalse(reading.valid)
        self.assertIn("Above maximum", reading.error)
        self.assertEqual(ingest.reading_count, 0)

    def test_on_message_stores_raw_fields(self):
        ingest = MQTTSensorIngest()
        reading = ingest.on_message("hive/beta/temperature", b"22.5")
        self.assertEqual(reading.raw_topic, "hive/beta/temperature")
        self.assertEqual(reading.raw_payload, "22.5")


class TestSensorReadingDataclass(unittest.TestCase):
    def test_defaults(self):
        r = SensorReading(hive_id="x", temperature_c=20.0)
        self.assertGreater(r.timestamp, 0)
        self.assertTrue(r.valid)
        self.assertEqual(r.error, "")

    def test_custom_fields(self):
        r = SensorReading(hive_id="y", temperature_c=15.0, valid=False, error="test err")
        self.assertFalse(r.valid)
        self.assertEqual(r.error, "test err")


if __name__ == "__main__":
    unittest.main()
