"""
JSONL-backed content store for replay.
Preserves document text alongside audit entries so replay is possible
even if ChromaDB data is lost.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterator, Optional

log = logging.getLogger("waggledance.replay_store")


class ReplayStore:
    """Append-only JSONL store for document text recovery."""

    MAX_REPLAY_SIZE_MB = 50

    def __init__(self, path: str = "data/replay_store.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def store(self, doc_id: str, text: str, content_hash: str,
              metadata: Optional[dict] = None) -> None:
        try:
            from core.disk_guard import check_disk_space
            check_disk_space(str(self.path.parent), label="ReplayStore")
        except (ImportError, OSError):
            pass
        entry = {
            "ts": time.time(),
            "doc_id": doc_id,
            "text": text,
            "content_hash": content_hash,
            "metadata": metadata or {},
        }
        self._maybe_rotate()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _maybe_rotate(self):
        """Rotate JSONL when file exceeds size threshold."""
        try:
            if self.path.exists() and os.path.getsize(self.path) > self.MAX_REPLAY_SIZE_MB * 1024 * 1024:
                with open(self.path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                half = len(lines) // 2
                with open(self.path, "w", encoding="utf-8") as f:
                    f.writelines(lines[half:])
                log.info("ReplayStore rotated: %d → %d entries", len(lines), len(lines) - half)
        except Exception as e:
            log.warning("ReplayStore rotation failed: %s", e)

    def _iter_all(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning(f"ReplayStore: skipping corrupt line {lineno}: {e}")

    def get_by_hash(self, content_hash: str) -> Optional[dict]:
        result = None
        for entry in self._iter_all():
            if entry.get("content_hash") == content_hash:
                result = entry  # return latest match
        return result

    def get_by_doc(self, doc_id: str) -> Optional[dict]:
        result = None
        for entry in self._iter_all():
            if entry.get("doc_id") == doc_id:
                result = entry
        return result

    def iter_range(self, start_ts: float, end_ts: float) -> Iterator[dict]:
        for entry in self._iter_all():
            ts = entry.get("ts", 0)
            if start_ts <= ts <= end_ts:
                yield entry
