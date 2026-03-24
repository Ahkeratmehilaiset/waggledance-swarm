"""GET /api/sensors, /api/sensors/home, /api/sensors/camera/events — stub data."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/sensors")
async def sensors_status():
    """Sensor hub status — stub returns all disabled."""
    return {
        "available": True,
        "status": {
            "mqtt": {"connected": False, "enabled": False, "subscriptions": 0},
            "home_assistant": {"connected": False, "enabled": False, "entities": 0},
            "frigate": {"connected": False, "enabled": False, "cameras": 0},
            "alerts": {"enabled": False, "pending": 0, "sent_total": 0},
        },
    }


@router.get("/api/sensors/home")
async def sensors_home():
    """Home Assistant entities — stub returns demo data."""
    return {
        "available": True,
        "entities": {
            "climate": [
                {"entity_id": "climate.olohuone", "state": "heat", "temperature": 21.5},
            ],
            "sensor": [
                {"entity_id": "sensor.ulkolampotila", "state": "-2.3", "unit": "\u00b0C"},
                {"entity_id": "sensor.sisalampotila", "state": "21.5", "unit": "\u00b0C"},
                {"entity_id": "sensor.kosteus", "state": "45", "unit": "%"},
            ],
            "light": [
                {"entity_id": "light.olohuone", "state": "on", "brightness": 180},
                {"entity_id": "light.keittio", "state": "off", "brightness": 0},
            ],
            "binary_sensor": [
                {"entity_id": "binary_sensor.etuovi", "state": "off"},
                {"entity_id": "binary_sensor.liike_piha", "state": "off"},
            ],
        },
        "context": "Olohuone: 21.5\u00b0C, l\u00e4mmitys p\u00e4\u00e4ll\u00e4. Ulkona: -2.3\u00b0C. Valot: olohuone p\u00e4\u00e4ll\u00e4 (70%), keitti\u00f6 pois.",
    }


@router.get("/api/sensors/camera/events")
async def sensors_camera_events():
    """Frigate camera events — stub returns demo data."""
    return {
        "available": True,
        "events": [
            {
                "camera": "piha_etukamera",
                "label": "ihminen",
                "label_en": "person",
                "score": 0.87,
                "severity": "info",
                "zones": ["piha"],
                "timestamp": "2026-03-04T10:15:00",
            },
            {
                "camera": "piha_takakamera",
                "label": "koira",
                "label_en": "dog",
                "score": 0.92,
                "severity": "medium",
                "zones": ["takapiha"],
                "timestamp": "2026-03-04T09:45:00",
            },
            {
                "camera": "tarha_kamera_1",
                "label": "karhu",
                "label_en": "bear",
                "score": 0.95,
                "severity": "critical",
                "zones": ["mehilaistarhaalue"],
                "timestamp": "2026-03-04T03:22:00",
            },
        ],
    }
