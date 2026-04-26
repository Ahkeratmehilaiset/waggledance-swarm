"""Pinned-input consumption layer — Phase 8.5 Session D.

Reads upstream session outputs (Session A curiosity, Session B
self-model, Session C dream, optional R7.5 resilience) under the
strict pinning rule from D.txt §PINNED INPUT MANIFEST RULE:

- only files listed in state.json's pinned_inputs are read
- only up to the recorded size_bytes per file
- never re-glob, never silently switch to fresher artifacts

Returns plain dicts / lists; this module performs no scoring.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _bounded_read(path: Path, byte_limit: int) -> bytes:
    if not path.exists():
        return b""
    with open(path, "rb") as f:
        return f.read(byte_limit)


def _find_pinned(pinned_inputs: list[dict], suffix: str) -> dict | None:
    for entry in pinned_inputs:
        path = entry.get("path", "")
        if path.endswith(suffix):
            return entry
    return None


def _find_all_pinned(pinned_inputs: list[dict], suffix: str) -> list[dict]:
    return [entry for entry in pinned_inputs
             if entry.get("path", "").endswith(suffix)]


def load_state(state_path: Path) -> tuple[str, list[dict], list[dict]]:
    """Read state.json and return
    (pinned_input_manifest_sha256, pinned_inputs, consumed_hook_contracts).
    """
    data = json.loads(state_path.read_text(encoding="utf-8"))
    return (
        data.get("pinned_input_manifest_sha256")
        or "sha256:unknown",
        data.get("pinned_inputs") or [],
        data.get("consumed_hook_contracts") or [],
    )


def load_self_model(pinned_inputs: list[dict]) -> dict | None:
    entry = _find_pinned(pinned_inputs, "self_model_snapshot.json")
    if entry is None:
        return None
    sz = int(entry.get("size_bytes") or 0)
    text = _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_curiosity_summary(pinned_inputs: list[dict]) -> dict | None:
    entry = _find_pinned(pinned_inputs, "curiosity_summary.json")
    if entry is None:
        return None
    sz = int(entry.get("size_bytes") or 0)
    text = _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_curiosity_log(pinned_inputs: list[dict]) -> list[dict]:
    entry = _find_pinned(pinned_inputs, "curiosity_log.jsonl")
    if entry is None:
        return []
    sz = int(entry.get("size_bytes") or 0)
    text = _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_calibration_corrections(pinned_inputs: list[dict]) -> list[dict]:
    entry = _find_pinned(pinned_inputs, "calibration_corrections.jsonl")
    if entry is None:
        return []
    sz = int(entry.get("size_bytes") or 0)
    text = _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_dream_meta_proposals(pinned_inputs: list[dict]) -> list[dict]:
    """Load every dream_meta_proposal.json pinned in the manifest."""
    entries = _find_all_pinned(pinned_inputs, "dream_meta_proposal.json")
    out: list[dict] = []
    for entry in entries:
        sz = int(entry.get("size_bytes") or 0)
        text = _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")
        try:
            out.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return out


def load_resilience_doc(pinned_inputs: list[dict]) -> str | None:
    """Optional R7.5 evidence — VECTOR_WRITER_RESILIENCE.md text."""
    entry = _find_pinned(pinned_inputs, "VECTOR_WRITER_RESILIENCE.md")
    if entry is None:
        return None
    sz = int(entry.get("size_bytes") or 0)
    return _bounded_read(Path(entry["path"]), sz).decode("utf-8", errors="replace")


# ── Hook-contract verification ───────────────────────────────────-

def validate_hook_contracts(consumed: list[dict],
                                repo_root: Path | None = None) -> list[str]:
    """Re-hash each consumed hook contract and reject mismatches.
    Returns a list of human-readable errors; empty = ok."""
    errors: list[str] = []
    for entry in consumed:
        path = entry.get("file")
        recorded = entry.get("file_sha256")
        version = entry.get("version")
        if not path or not recorded or version is None:
            errors.append(f"hook contract missing required fields: {entry}")
            continue
        candidates: list[Path] = []
        if repo_root is not None:
            candidates.append(repo_root / path)
        candidates.append(Path(path))
        full: Path | None = None
        for c in candidates:
            if c.exists():
                full = c
                break
        if full is None:
            errors.append(f"hook contract file missing on disk: {path}")
            continue
        actual = "sha256:" + hashlib.sha256(full.read_bytes()).hexdigest()
        if actual != recorded:
            errors.append(
                f"hook contract sha mismatch for {path}: "
                f"recorded={recorded} actual={actual}"
            )
    return errors
