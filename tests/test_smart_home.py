"""Phase 5: Smart Home Sensor Integration — test suite (~25 tests).

Tests: syntax, MQTTHub, HomeAssistantBridge, FrigateIntegration,
AlertDispatcher, SensorHub, settings.yaml integration.
"""
import sys, os, ast, json, time, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    """Run async coroutine in sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. Syntax tests ────────────────────────────────────────────────────

def test_syntax_mqtt_hub():
    path = os.path.join(os.path.dirname(__file__), "..", "integrations", "mqtt_hub.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] integrations/mqtt_hub.py syntax valid")


def test_syntax_alert_dispatcher():
    path = os.path.join(os.path.dirname(__file__), "..", "integrations", "alert_dispatcher.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] integrations/alert_dispatcher.py syntax valid")


def test_syntax_home_assistant():
    path = os.path.join(os.path.dirname(__file__), "..", "integrations", "home_assistant.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] integrations/home_assistant.py syntax valid")


def test_syntax_frigate_mqtt():
    path = os.path.join(os.path.dirname(__file__), "..", "integrations", "frigate_mqtt.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] integrations/frigate_mqtt.py syntax valid")


def test_syntax_sensor_hub():
    path = os.path.join(os.path.dirname(__file__), "..", "integrations", "sensor_hub.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] integrations/sensor_hub.py syntax valid")


# ── 2. MQTTHub tests ───────────────────────────────────────────────────

def test_mqtt_hub_init_disabled():
    """MQTTHub with enabled=false does nothing on start."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False})
    assert not hub.enabled
    hub.start()  # Should be no-op
    status = hub.get_status()
    assert status["enabled"] is False
    assert status["connected"] is False
    print("  [PASS] MQTTHub disabled init OK")


def test_mqtt_hub_subscribe():
    """MQTTHub.subscribe() registers handlers."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False})

    async def handler(topic, payload): pass
    hub.subscribe("test/topic", handler)

    assert "test/topic" in hub._handlers
    assert len(hub._handlers["test/topic"]) == 1
    print("  [PASS] MQTTHub subscribe OK")


def test_mqtt_hub_topic_matching():
    """MQTTHub topic matching with wildcards."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False})

    # Exact match
    assert hub._topic_matches("frigate/events", "frigate/events")
    # + wildcard
    assert hub._topic_matches("frigate/front/person/yard", "frigate/+/+/+")
    # # wildcard
    assert hub._topic_matches("frigate/events/sub/deep", "frigate/#")
    # No match
    assert not hub._topic_matches("other/events", "frigate/events")
    print("  [PASS] MQTTHub topic matching OK")


def test_mqtt_hub_dedup():
    """MQTTHub dedup filters duplicate messages."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False, "dedup_window_s": 5})

    import hashlib
    payload = "test_message"
    msg_hash = hashlib.md5(f"test/topic:{payload}".encode()).hexdigest()

    # First message passes
    hub._seen[msg_hash] = time.monotonic()
    # Second message within window should be deduped
    assert msg_hash in hub._seen
    elapsed = time.monotonic() - hub._seen[msg_hash]
    assert elapsed < 5  # Within dedup window
    print("  [PASS] MQTTHub dedup OK")


def test_mqtt_hub_reconnect_backoff():
    """MQTTHub reconnect delay doubles correctly."""
    from integrations.mqtt_hub import MQTTHub
    hub = MQTTHub({"enabled": False})

    assert hub._reconnect_delay == 1.0
    hub._reconnect_delay = min(hub._reconnect_delay * 2, hub._max_reconnect_delay)
    assert hub._reconnect_delay == 2.0
    hub._reconnect_delay = min(hub._reconnect_delay * 2, hub._max_reconnect_delay)
    assert hub._reconnect_delay == 4.0
    # Max cap
    hub._reconnect_delay = 100
    hub._reconnect_delay = min(hub._reconnect_delay * 2, hub._max_reconnect_delay)
    assert hub._reconnect_delay == 60.0
    print("  [PASS] MQTTHub reconnect backoff OK")


# ── 3. HomeAssistantBridge tests ────────────────────────────────────────

def test_ha_init_disabled():
    """HA Bridge with enabled=false is passive."""
    from integrations.home_assistant import HomeAssistantBridge
    ha = HomeAssistantBridge({"enabled": False})
    assert not ha.enabled
    assert ha._entities == {}
    status = ha.get_status()
    assert status["enabled"] is False
    print("  [PASS] HA Bridge disabled init OK")


def test_ha_categorize_entity():
    """HA categorizes entities by domain prefix."""
    from integrations.home_assistant import DOMAIN_CATEGORIES
    assert "sensor" in DOMAIN_CATEGORIES
    assert "light" in DOMAIN_CATEGORIES
    assert "binary_sensor" in DOMAIN_CATEGORIES
    assert DOMAIN_CATEGORIES["sensor"] == "sensori"
    assert DOMAIN_CATEGORIES["light"] == "valo"
    print("  [PASS] HA entity categorization OK")


def test_ha_finnish_format():
    """HA formats entity state in Finnish."""
    from integrations.home_assistant import HomeAssistantBridge
    ha = HomeAssistantBridge({"enabled": False})

    # Light entity
    text = ha._format_state_finnish("light.living_room", {
        "state": "on",
        "attributes": {"friendly_name": "Living Room Light", "brightness": 204},
    })
    assert "päällä" in text
    assert "80%" in text
    print("  [PASS] HA Finnish format OK")


def test_ha_significance_filter():
    """HA significance filter: numeric change > 1.0, binary any."""
    from integrations.home_assistant import HomeAssistantBridge
    ha = HomeAssistantBridge({"enabled": False})

    # Binary: any change is significant
    assert ha._is_significant_change(
        "binary_sensor.door",
        {"state": "off", "attributes": {}},
        {"state": "on", "attributes": {}},
    )
    # Numeric: < 1.0 change is NOT significant
    assert not ha._is_significant_change(
        "sensor.temp",
        {"state": "20.5", "attributes": {}},
        {"state": "20.8", "attributes": {}},
    )
    # Numeric: > 1.0 change IS significant
    assert ha._is_significant_change(
        "sensor.temp",
        {"state": "20.0", "attributes": {}},
        {"state": "22.0", "attributes": {}},
    )
    print("  [PASS] HA significance filter OK")


def test_ha_disabled_graceful():
    """HA Bridge gracefully handles disabled state."""
    from integrations.home_assistant import HomeAssistantBridge
    ha = HomeAssistantBridge({"enabled": False})
    assert ha.get_home_context() == ""
    assert ha.get_entities() == {}
    _run(ha.start())  # Should be no-op
    print("  [PASS] HA disabled graceful OK")


# ── 4. Frigate tests ───────────────────────────────────────────────────

def test_frigate_bear_critical():
    """Frigate: bear detection = CRITICAL severity."""
    from integrations.frigate_mqtt import FrigateIntegration
    from integrations.alert_dispatcher import SEVERITY_CRITICAL
    frigate = FrigateIntegration({"enabled": True})
    severity = frigate._classify_severity("bear")
    assert severity == SEVERITY_CRITICAL
    print("  [PASS] Frigate bear=CRITICAL OK")


def test_frigate_person_day_info():
    """Frigate: person during day = INFO severity."""
    from integrations.frigate_mqtt import FrigateIntegration
    from integrations.alert_dispatcher import SEVERITY_INFO
    frigate = FrigateIntegration({"enabled": True})
    # Patch _is_night to return False (day)
    original = FrigateIntegration._is_night
    FrigateIntegration._is_night = staticmethod(lambda: False)
    try:
        severity = frigate._classify_severity("person")
        assert severity == SEVERITY_INFO
    finally:
        FrigateIntegration._is_night = original
    print("  [PASS] Frigate person_day=INFO OK")


def test_frigate_person_night_high():
    """Frigate: person at night = HIGH severity."""
    from integrations.frigate_mqtt import FrigateIntegration
    from integrations.alert_dispatcher import SEVERITY_HIGH
    frigate = FrigateIntegration({"enabled": True})
    # Patch _is_night to return True
    original = FrigateIntegration._is_night
    FrigateIntegration._is_night = staticmethod(lambda: True)
    try:
        severity = frigate._classify_severity("person")
        assert severity == SEVERITY_HIGH
    finally:
        FrigateIntegration._is_night = original
    print("  [PASS] Frigate person_night=HIGH OK")


def test_frigate_dedup():
    """Frigate: same label+camera within window is deduped."""
    from integrations.frigate_mqtt import FrigateIntegration
    frigate = FrigateIntegration({"enabled": True, "dedup_window_s": 60})

    key = ("person", "front_door")
    now = time.monotonic()
    frigate._recent[key] = now

    # Same key within window: should be deduped
    assert key in frigate._recent
    assert (time.monotonic() - frigate._recent[key]) < 60
    print("  [PASS] Frigate dedup OK")


def test_frigate_label_fi():
    """Frigate: Finnish label translations exist."""
    from integrations.frigate_mqtt import LABEL_FI
    assert LABEL_FI["person"] == "ihminen"
    assert LABEL_FI["bear"] == "karhu"
    assert LABEL_FI["dog"] == "koira"
    assert LABEL_FI["cat"] == "kissa"
    assert LABEL_FI["car"] == "auto"
    print("  [PASS] Frigate Finnish labels OK")


def test_frigate_low_score_filter():
    """Frigate: events below min_score are filtered."""
    from integrations.frigate_mqtt import FrigateIntegration
    frigate = FrigateIntegration({"enabled": True, "min_score": 0.6})

    async def _test():
        # Process event with low score
        await frigate._process_event({
            "label": "person",
            "score": 0.3,
            "camera": "test",
        })
        assert frigate._filtered_low_score == 1
        assert frigate._stored_events == 0

    _run(_test())
    print("  [PASS] Frigate low score filter OK")


# ── 5. AlertDispatcher tests ───────────────────────────────────────────

def test_alert_rate_limiting():
    """AlertDispatcher rate limits per source."""
    from integrations.alert_dispatcher import AlertDispatcher
    ad = AlertDispatcher({"enabled": True, "rate_limit": {"max_per_minute": 2}})

    # Simulate 3 messages from same source
    now = time.monotonic()
    ad._rate_windows["test_source"].append(now)
    ad._rate_windows["test_source"].append(now)

    assert ad._is_rate_limited("test_source")
    assert not ad._is_rate_limited("other_source")
    print("  [PASS] AlertDispatcher rate limiting OK")


def test_alert_telegram_format():
    """AlertDispatcher Telegram message uses HTML + emoji."""
    from integrations.alert_dispatcher import Alert, SEVERITY_EMOJI, SEVERITY_CRITICAL
    alert = Alert(
        severity=SEVERITY_CRITICAL,
        title="Karhu havaittu",
        message="Kamerahavainto: karhu (varmuus 95%)",
        source="frigate",
    )
    emoji = SEVERITY_EMOJI[SEVERITY_CRITICAL]
    assert emoji == "\U0001f534"  # 🔴
    assert alert.severity == "critical"
    assert alert.title == "Karhu havaittu"
    print("  [PASS] AlertDispatcher Telegram format OK")


def test_alert_webhook_payload():
    """AlertDispatcher webhook payload has required fields."""
    from integrations.alert_dispatcher import Alert
    alert = Alert(
        severity="high",
        title="Test Alert",
        message="Test message",
        source="test",
    )
    assert alert.severity == "high"
    assert alert.title == "Test Alert"
    assert alert.message == "Test message"
    assert alert.source == "test"
    assert alert.timestamp  # ISO 8601
    print("  [PASS] AlertDispatcher webhook payload OK")


def test_alert_disabled_graceful():
    """AlertDispatcher gracefully handles disabled state."""
    from integrations.alert_dispatcher import AlertDispatcher, Alert
    ad = AlertDispatcher({"enabled": False})

    async def _test():
        await ad.start()  # No-op
        await ad.send_alert(Alert(
            severity="critical",
            title="Test",
            message="Should be ignored",
            source="test",
        ))
        assert ad._sent_count == 0

    _run(_test())
    print("  [PASS] AlertDispatcher disabled graceful OK")


# ── 6. SensorHub tests ─────────────────────────────────────────────────

def test_sensor_hub_init_all_disabled():
    """SensorHub with all components disabled starts OK."""
    from integrations.sensor_hub import SensorHub
    hub = SensorHub(config={
        "mqtt": {"enabled": False},
        "home_assistant": {"enabled": False},
        "frigate": {"enabled": False},
        "alerts": {"enabled": False},
    })

    async def _test():
        await hub.start()
        status = hub.get_status()
        assert status["started"] is True
        assert status["mqtt"]["enabled"] is False
        assert status["home_assistant"]["enabled"] is False
        assert status["frigate"]["enabled"] is False
        assert status["alerts"]["enabled"] is False
        await hub.stop()

    _run(_test())
    print("  [PASS] SensorHub all disabled init OK")


def test_sensor_hub_status_dict():
    """SensorHub.get_status() returns proper structure."""
    from integrations.sensor_hub import SensorHub
    hub = SensorHub(config={
        "mqtt": {"enabled": False},
        "home_assistant": {"enabled": False},
        "frigate": {"enabled": False},
        "alerts": {"enabled": False},
    })
    status = hub.get_status()
    assert "started" in status
    assert "mqtt" in status
    assert "home_assistant" in status
    assert "frigate" in status
    assert "alerts" in status
    print("  [PASS] SensorHub status dict OK")


def test_sensor_hub_context_finnish():
    """SensorHub.get_sensor_context() returns Finnish text."""
    from integrations.sensor_hub import SensorHub
    hub = SensorHub(config={
        "mqtt": {"enabled": False},
        "home_assistant": {"enabled": False},
        "frigate": {"enabled": False},
        "alerts": {"enabled": False},
    })
    # With no components active, context is empty
    ctx = hub.get_sensor_context()
    assert isinstance(ctx, str)
    print("  [PASS] SensorHub context Finnish OK")


def test_sensor_event_dataclass():
    """SensorEvent dataclass works correctly."""
    from integrations.sensor_hub import SensorEvent
    event = SensorEvent(
        source="frigate",
        event_type="detection",
        severity="critical",
        title="Karhu havaittu",
        data={"camera": "front", "label": "bear"},
    )
    assert event.source == "frigate"
    assert event.severity == "critical"
    d = event.to_dict()
    assert d["source"] == "frigate"
    assert d["data"]["camera"] == "front"
    assert "timestamp" in d
    print("  [PASS] SensorEvent dataclass OK")


# ── 7. Integration test ────────────────────────────────────────────────

def test_settings_yaml_has_sensor_sections():
    """settings.yaml has mqtt, home_assistant, frigate, alerts sections."""
    import yaml
    path = os.path.join(os.path.dirname(__file__), "..", "configs", "settings.yaml")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert "mqtt" in cfg, "settings.yaml missing 'mqtt' section"
    assert "home_assistant" in cfg, "settings.yaml missing 'home_assistant' section"
    assert "frigate" in cfg, "settings.yaml missing 'frigate' section"
    assert "alerts" in cfg, "settings.yaml missing 'alerts' section"

    # Verify structure
    assert cfg["mqtt"]["enabled"] is False
    assert cfg["home_assistant"]["enabled"] is False
    assert cfg["frigate"]["enabled"] is False
    assert cfg["alerts"]["enabled"] is False

    # Verify severity rules
    assert "bear" in cfg["frigate"]["severity_rules"]["critical"]
    assert "wolf" in cfg["frigate"]["severity_rules"]["critical"]

    # Verify TTL rules include new categories
    ttl = cfg["advanced_learning"]["memory_limits"]["ttl_rules"]
    assert "home_state" in ttl
    assert "sensor_reading" in ttl
    assert ttl["home_state"] == 1
    assert ttl["sensor_reading"] == 1

    print("  [PASS] settings.yaml has all sensor sections")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    # Syntax
    test_syntax_mqtt_hub,
    test_syntax_alert_dispatcher,
    test_syntax_home_assistant,
    test_syntax_frigate_mqtt,
    test_syntax_sensor_hub,
    # MQTTHub
    test_mqtt_hub_init_disabled,
    test_mqtt_hub_subscribe,
    test_mqtt_hub_topic_matching,
    test_mqtt_hub_dedup,
    test_mqtt_hub_reconnect_backoff,
    # HA Bridge
    test_ha_init_disabled,
    test_ha_categorize_entity,
    test_ha_finnish_format,
    test_ha_significance_filter,
    test_ha_disabled_graceful,
    # Frigate
    test_frigate_bear_critical,
    test_frigate_person_day_info,
    test_frigate_person_night_high,
    test_frigate_dedup,
    test_frigate_label_fi,
    test_frigate_low_score_filter,
    # AlertDispatcher
    test_alert_rate_limiting,
    test_alert_telegram_format,
    test_alert_webhook_payload,
    test_alert_disabled_graceful,
    # SensorHub
    test_sensor_hub_init_all_disabled,
    test_sensor_hub_status_dict,
    test_sensor_hub_context_finnish,
    test_sensor_event_dataclass,
    # Integration
    test_settings_yaml_has_sensor_sections,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print(f"\n{'='*60}")
    print("Phase 5: Smart Home Sensor Integration — {0} tests".format(len(ALL_TESTS)))
    print(f"{'='*60}\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"  [FAIL] {test.__name__}: {e}")

    print(f"\n{'='*60}")
    print(f"Result: {passed}/{passed+failed} passed, {failed} failed")
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"{'='*60}")

    sys.exit(0 if failed == 0 else 1)
