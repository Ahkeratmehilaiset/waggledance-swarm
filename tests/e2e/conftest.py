"""Shared fixtures for e2e Playwright tests."""
import os
import tempfile

import pytest

BASE_URL = os.environ.get("GAUNTLET_BASE_URL", "http://127.0.0.1:8002")

def _load_api_key() -> str:
    kf = os.path.join(tempfile.gettempdir(), "waggle_gauntlet_8002.key")
    if os.path.isfile(kf):
        with open(kf, "r") as f:
            return f.read().strip()
    return ""

API_KEY = _load_api_key()

TABS = [
    "overview", "memory", "reasoning", "micro", "learning",
    "feeds", "ops", "mesh", "trace", "magma", "chat",
]

VIEWPORTS = [
    {"width": 1280, "height": 720},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

ARTIFACT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "runs", "ui_gauntlet_20260412",
)
SCREENSHOT_DIR = os.path.join(ARTIFACT_DIR, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
