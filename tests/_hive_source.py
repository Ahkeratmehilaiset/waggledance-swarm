"""Helper to read HiveMind source from all modules (pre/post refactor).

After the v0.9.0 refactor, methods are extracted from hivemind.py to:
  core/chat_handler.py
  core/night_mode_controller.py
  core/round_table_controller.py
  core/heartbeat_controller.py

After v3.3 refactor, chat_handler.py is split into:
  core/chat_preprocessing.py
  core/chat_routing_engine.py
  core/chat_delegation.py
  core/chat_telemetry.py

Tests that check source patterns should use read_hive_source() to get
the combined source from all modules. This works both before the refactor
(only hivemind.py exists) and after (all modules exist).
"""
import os

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

_HIVE_MODULES = [
    "hivemind.py",
    os.path.join("core", "chat_handler.py"),
    os.path.join("core", "chat_preprocessing.py"),
    os.path.join("core", "chat_routing_engine.py"),
    os.path.join("core", "chat_delegation.py"),
    os.path.join("core", "chat_telemetry.py"),
    os.path.join("core", "night_mode_controller.py"),
    os.path.join("core", "round_table_controller.py"),
    os.path.join("core", "heartbeat_controller.py"),
]


def read_hive_source():
    """Read combined source from hivemind.py + all extracted modules."""
    parts = []
    for fname in _HIVE_MODULES:
        path = os.path.join(_ROOT, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                parts.append(f.read())
    return "\n".join(parts)


def hive_source_files():
    """Return list of existing hive source file paths."""
    return [os.path.join(_ROOT, f) for f in _HIVE_MODULES
            if os.path.exists(os.path.join(_ROOT, f))]
