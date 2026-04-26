"""Hologram snapshot persistence — Phase 9 §P.

Atomic-write the rendered RealitySnapshot to disk. Same tmp+replace
pattern as R7.5 §G.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .reality_view import RealitySnapshot


def save_snapshot(snapshot: RealitySnapshot,
                       path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".reality.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def load_snapshot_dict(path: Path | str) -> dict | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
