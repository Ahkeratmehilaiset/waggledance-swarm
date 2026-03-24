"""GET /api/sensors/audio, /api/sensors/audio/bee — stub data for dashboard dev."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/sensors/audio")
async def sensors_audio():
    """Audio monitor status — stub returns demo data."""
    return {
        "available": True,
        "status": {
            "enabled": False,
            "started": False,
            "uptime_s": None,
            "total_events": 3,
            "total_spectrums": 42,
            "recent_events": 3,
            "bee_analyzer": {
                "total_analyses": 42,
                "anomalies_detected": 1,
                "hives_tracked": 2,
                "baseline_entries": 84,
            },
            "bird_monitor": {
                "enabled": False,
                "model_loaded": False,
                "total_classifications": 0,
            },
        },
        "events": [
            {
                "type": "bee_spectrum",
                "hive_id": "pesa_01",
                "status": "normal",
                "stress_level": 0.12,
                "fundamental_hz": 248.5,
                "description_fi": "Normaali humina",
                "timestamp": "2026-03-04T12:00:00",
            },
            {
                "type": "bee_spectrum",
                "hive_id": "pesa_02",
                "status": "stressed",
                "stress_level": 0.65,
                "fundamental_hz": 312.0,
                "description_fi": "Stressaantunut \u2014 perustaajuus kohonnut",
                "timestamp": "2026-03-04T11:45:00",
            },
        ],
    }


@router.get("/api/sensors/audio/bee")
async def sensors_audio_bee():
    """Bee health per hive — stub returns demo data."""
    return {
        "available": True,
        "hives": {
            "pesa_01": {
                "hive_id": "pesa_01",
                "status": "normal",
                "stress_level": 0.12,
                "fundamental_hz": 248.5,
                "anomaly": False,
                "confidence": 0.95,
                "description_fi": "Normaali humina",
            },
            "pesa_02": {
                "hive_id": "pesa_02",
                "status": "stressed",
                "stress_level": 0.65,
                "fundamental_hz": 312.0,
                "anomaly": True,
                "confidence": 0.80,
                "description_fi": "Stressaantunut \u2014 perustaajuus kohonnut",
            },
        },
        "stats": {
            "total_analyses": 42,
            "anomalies_detected": 1,
            "hives_tracked": 2,
            "baseline_entries": 84,
        },
    }
