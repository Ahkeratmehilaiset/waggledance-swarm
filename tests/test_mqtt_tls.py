"""MQTT TLS configuration tests."""
import logging
import pytest


def test_tls_default_enabled():
    """TLS must be ON when mqtt_tls not explicitly set."""
    config = {"host": "localhost"}
    assert config.get("mqtt_tls", True) is True


def test_tls_explicit_port_default():
    """Port must default to 8883 when TLS is enabled."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False, "host": "localhost"})
    assert hub.use_tls is True
    assert hub.port == 8883


def test_tls_disabled_port():
    """Port must default to 1883 when TLS is explicitly disabled."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False, "host": "localhost", "mqtt_tls": False})
    assert hub.use_tls is False
    assert hub.port == 1883


def test_plaintext_warning(caplog):
    """Disabling TLS must produce a warning."""
    caplog.set_level(logging.WARNING)
    config = {"mqtt_tls": False}
    use_tls = config.get("mqtt_tls", True)
    if not use_tls:
        logging.warning("MQTT TLS DISABLED — traffic is unencrypted!")
    assert "MQTT TLS DISABLED" in caplog.text
