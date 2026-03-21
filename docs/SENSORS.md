# WaggleDance Sensors & Integrations

*Anturit ja integraatiot — IoT, audio, and voice systems*

All sensors are **disabled by default** in `configs/settings.yaml`. Enable individually as needed.

## Architecture

```
+---------------------------------------------------+
|                  SensorHub                         |
|        (integrations/sensor_hub.py)                |
|                                                    |
|  Init order: AlertDispatcher -> MQTT -> Frigate    |
|              -> HomeAssistant -> AudioMonitor      |
|                                                    |
|  Provides: aggregated status, Finnish context      |
|            for agents, WebSocket broadcast          |
+-------------------+-------------------------------+
                    |
    +-------+-------+-------+----------+
    v       v       v       v          v
  MQTT   Frigate   HA    Audio      Voice
  Hub     NVR    Bridge  Monitor   Interface
```

---

## MQTT Hub (`integrations/mqtt_hub.py`)

Central message broker integration using paho-mqtt:
- Background thread with asyncio bridge
- MD5 message deduplication (configurable window)
- Exponential reconnect backoff (1s -> 60s)
- Subscribes to configured topics for all sensor subsystems

**Config** (`settings.yaml`):
```yaml
mqtt:
  enabled: false
  broker: "localhost"
  port: 8883
  mqtt_tls: true
```

---

## Frigate NVR (`integrations/frigate_mqtt.py`)

Camera event processing from Frigate NVR via MQTT:
- Severity classification: bear/wolf = CRITICAL, person at night = HIGH, dog/cat = MEDIUM, person/car daytime = INFO
- Finnish label translations (karhu, susi, henkilö, auto...)
- 60-second deduplication per label+camera
- Alerts dispatched for HIGH+ severity

**Config:**
```yaml
frigate:
  enabled: false
  mqtt_topic: "frigate/events"
```

**API:** `GET /api/sensors/camera/events`

---

## Home Assistant (`integrations/home_assistant.py`)

REST API polling bridge:
- Auto entity discovery by domain (light, sensor, switch, climate)
- Significance filter: numeric >1.0 change, binary any change, brightness >20%
- Finnish state formatting: "Olohuoneen valo: päällä (80%)"

**Config:**
```yaml
home_assistant:
  enabled: false
  url: "http://homeassistant.local:8123"
  # Token via WAGGLEDANCE_HA_TOKEN env var
```

**API:** `GET /api/sensors/home`

---

## Audio Sensors

### Audio Analyzer (`integrations/audio_monitor.py`)

ESP32-based environmental audio analysis via MQTT:
- FFT spectrum analysis (frequency distribution)
- 7-day rolling baseline per sensor node
- **Anomaly detection:** frequency shift from baseline
- **Alert classification:** broad energy increase in target bands
- Finnish status descriptions

### Bird Monitor (`integrations/bird_monitor.py`)

BirdNET-Lite integration for bird species detection:
- Graceful degradation without BirdNET model
- Predator detection: bear, wolf, eagle, hawk, owl, fox
- Finnish species name mapping
- Alerts on predator detection

**Config:**
```yaml
audio:
  audio_analyzer:
    enabled: false
  bird_monitor:
    enabled: false
  mqtt_topics:
    spectrum: "waggledance/audio/+/spectrum"
    event: "waggledance/audio/+/event"
```

**API:** `GET /api/sensors/audio`

---

## Voice Interface

### Whisper STT (`integrations/whisper_stt.py`)

Speech-to-text using OpenAI Whisper (local, no cloud):
- Model: whisper-small (CPU)
- Language: Finnish
- Wake word: "Hei WaggleDance"
- Voice Activity Detection: 1.5s silence threshold

### Piper TTS (`integrations/piper_tts.py`)

Text-to-speech using Piper:
- Voice: fi_FI-harri-medium
- Output: WAV format
- Async synthesis

### Voice Interface (`integrations/voice_interface.py`)

Orchestrator combining STT + TTS:
- Audio pipeline: Mic -> STT -> Chat -> TTS -> Speaker
- Text pipeline: API text -> Chat -> TTS
- Graceful degradation without models

**Config:**
```yaml
voice:
  enabled: false
  wake_word: "hei waggledance"
  language: "fi"
```

**API:** `GET /api/voice/status`, `POST /api/voice/text`, `POST /api/voice/audio`

---

## Alert Dispatcher (`integrations/alert_dispatcher.py`)

Sends alerts from all sensor subsystems:
- **Telegram:** HTML-formatted messages with emoji severity indicators
- **Webhook:** JSON POST with 3x retry
- Sliding window rate limiting (default 5/min/source)
- Async queue + consumer loop

**Config:**
```yaml
alerts:
  enabled: false
  telegram:
    enabled: false
    # Token via WAGGLEDANCE_TELEGRAM_BOT_TOKEN env var
    # Chat ID via WAGGLEDANCE_TELEGRAM_CHAT_ID env var
  webhook:
    enabled: false
    url: ""
```

---

## External Data Feeds

Not strictly sensors, but provide environmental context:

| Feed | Source | Data |
|------|--------|------|
| FMI Weather | Finnish Meteorological Institute | Temperature, wind, precipitation |
| Electricity | Spot price API | Current price (snt/kWh) |
| RSS Feeds | Ruokavirasto, FMI | Food safety, weather alerts |

Managed by `DataFeedScheduler`. Configured in `settings.yaml` under `feeds:`.
