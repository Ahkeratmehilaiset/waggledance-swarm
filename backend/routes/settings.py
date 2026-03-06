"""GET/POST /api/settings — Runtime configuration."""
import os
import tempfile
import yaml
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS_FILE = _PROJECT_ROOT / "configs" / "settings.yaml"

# Toggleable feature keys (dot paths into settings.yaml)
_TOGGLEABLE = {
    "feeds.enabled", "feeds.weather.enabled", "feeds.electricity.enabled",
    "feeds.rss.enabled", "mqtt.enabled", "home_assistant.enabled",
    "frigate.enabled", "alerts.enabled", "voice.enabled", "audio.enabled",
    "micro_model.v2.enabled", "micro_model.v3.enabled",
}


def _load_settings() -> dict:
    """Load settings.yaml."""
    if not _SETTINGS_FILE.exists():
        return {}
    with open(_SETTINGS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_nested(d: dict, path: str):
    """Get value at dot-separated path."""
    keys = path.split(".")
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _set_nested(d: dict, path: str, value):
    """Set value at dot-separated path."""
    keys = path.split(".")
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


@router.get("/api/settings")
async def get_settings():
    """Return current feature toggles from settings.yaml."""
    cfg = _load_settings()

    toggles = {}
    for path in sorted(_TOGGLEABLE):
        val = _get_nested(cfg, path)
        toggles[path] = bool(val) if val is not None else False

    return {
        "toggles": toggles,
        "elastic_scaling": cfg.get("elastic_scaling", {}),
        "heartbeat_interval": cfg.get("hivemind", {}).get("heartbeat_interval", 30),
    }


class SettingsToggleRequest(BaseModel):
    key: str
    value: bool


@router.post("/api/settings/toggle")
async def toggle_setting(body: SettingsToggleRequest):
    """Toggle a feature on/off. Body: {"key": "feeds.enabled", "value": true}"""
    key = body.key
    value = body.value

    if key not in _TOGGLEABLE:
        return {"error": f"Key '{key}' is not toggleable", "allowed": sorted(_TOGGLEABLE)}

    cfg = _load_settings()
    _set_nested(cfg, key, value)

    # Atomic write to prevent corruption
    fd, tmp = tempfile.mkstemp(dir=str(_SETTINGS_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(_SETTINGS_FILE))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {"ok": True, "key": key, "value": value}
