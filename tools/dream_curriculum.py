#!/usr/bin/env python3
"""Dream curriculum planner — Phase 8.5 Session C, deliverable C.1.

Reads pinned Session B outputs (self_model_snapshot, tensions,
calibration_corrections) plus Session A curiosity outputs and emits
a deterministic 7-night dream curriculum.

Runtime safety: zero touch. No port 8002. No live LLM calls. No
runtime mutation. Crown-jewel area is `waggledance/core/dreaming/*`.

CLI:
  python tools/dream_curriculum.py --help
  python tools/dream_curriculum.py                       # dry-run
  python tools/dream_curriculum.py --apply               # write
  python tools/dream_curriculum.py --input-manifest PATH
  python tools/dream_curriculum.py --apply --top-nights 7
  python tools/dream_curriculum.py --apply --real-data-only
  python tools/dream_curriculum.py --apply --cell thermal
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.dreaming import curriculum as cu  # noqa: E402

DEFAULT_OUT_ROOT = ROOT / "docs" / "runs" / "dream"


# ── Pin loading ───────────────────────────────────────────────────

def load_pin_manifest(state_or_manifest_path: Path) -> tuple[str, list[dict]]:
    """Read state.json (or dedicated manifest) and return
    (pin_hash, pinned_inputs)."""
    data = json.loads(state_or_manifest_path.read_text(encoding="utf-8"))
    pin_hash = (
        data.get("pinned_input_manifest_sha256")
        or data.get("pinned_artifact_manifest_sha256")
        or "sha256:unknown"
    )
    raw = data.get("pinned_inputs") or data.get("pinned_artifacts") or []
    return pin_hash, raw


def _bounded_read(path: Path, byte_limit: int) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        return f.read(byte_limit).decode("utf-8", errors="replace")


def _find_pinned(pinned_inputs: list[dict], suffix: str) -> dict | None:
    for entry in pinned_inputs:
        path = entry.get("path") or entry.get("relpath", "")
        if path.endswith(suffix):
            return entry
    return None


def _load_self_model(pinned_inputs: list[dict]) -> dict | None:
    """Self-model snapshot is referenced by Session B state.json. Look
    for any pinned input ending in self_model_snapshot.json."""
    entry = _find_pinned(pinned_inputs, "self_model_snapshot.json")
    if entry is None:
        # Try alternative: the Session B state file pins outputs at
        # docs/runs/self_model/<sha12>/. Scan by directory hint.
        return None
    sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
    text = _bounded_read(ROOT / entry["path"], sz)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_curiosity_log(pinned_inputs: list[dict]) -> list[dict]:
    entry = _find_pinned(pinned_inputs, "curiosity_log.jsonl")
    if entry is None:
        return []
    sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
    text = _bounded_read(ROOT / entry["path"], sz)
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


def _load_calibration_corrections(pinned_inputs: list[dict]) -> list[dict]:
    entry = _find_pinned(pinned_inputs, "calibration_corrections.jsonl")
    if entry is None:
        return []
    sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
    text = _bounded_read(ROOT / entry["path"], sz)
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


# ── Pipeline ──────────────────────────────────────────────────────

def build(
    self_model: dict,
    curiosity_log: list[dict],
    calibration_corrections: list[dict],
    branch_name: str,
    base_commit_hash: str,
    pin_hash: str,
    top_nights: int = 7,
    cell_filter: str | None = None,
) -> cu.DreamCurriculum:
    c = cu.build_curriculum(
        self_model=self_model,
        curiosity_log=curiosity_log,
        calibration_corrections=calibration_corrections,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pin_hash,
        top_nights=top_nights,
    )
    if cell_filter:
        # Filter nights to only those targeting the chosen cell
        nights = tuple(
            n for n in c.nights
            if n.target_items and any(
                it.candidate_cell == cell_filter for it in n.target_items
            )
        )
        c = cu.DreamCurriculum(
            schema_version=c.schema_version,
            branch_name=c.branch_name,
            base_commit_hash=c.base_commit_hash,
            pinned_input_manifest_sha256=c.pinned_input_manifest_sha256,
            primary_source=c.primary_source,
            nights=nights,
            counts_by_mode=c.counts_by_mode,
            counts_by_kind=c.counts_by_kind,
            secondary_fallback_reason=c.secondary_fallback_reason,
        )
    return c


def emit(c: cu.DreamCurriculum, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = cu.curriculum_to_dict(c)
    json_path = out_dir / "dream_curriculum.json"
    md_path = out_dir / "dream_curriculum.md"
    json_path.write_text(
        json.dumps(d, indent=2, sort_keys=True), encoding="utf-8",
    )
    md_path.write_text(cu.render_curriculum_md(d), encoding="utf-8")
    return {"curriculum_json": json_path, "curriculum_md": md_path}


# ── Branch / commit detection ─────────────────────────────────────

def _detect_branch() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        return text[5:].strip().rsplit("/", 1)[-1]
    return ""


def _detect_base_commit() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        ref_path = ROOT / ".git" / text[5:].strip()
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""
    return text or ""


# ── CLI ──────────────────────────────────────────────────────────

def _default_out_dir(pin_hash: str) -> Path:
    sha12 = pin_hash.replace("sha256:", "")[:12]
    return DEFAULT_OUT_ROOT / sha12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-manifest", type=Path,
                    default=ROOT / "docs" / "runs" / "phase8_5_dream_session_state.json")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--real-data-only", action="store_true")
    ap.add_argument("--cell", type=str, default=None)
    ap.add_argument("--top-nights", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.input_manifest.exists():
        print(f"input manifest missing: {args.input_manifest}", file=sys.stderr)
        return 2 if args.real_data_only else 1
    pin_hash, pinned = load_pin_manifest(args.input_manifest)

    # Real-data-only enforcement
    if args.real_data_only:
        for entry in pinned:
            ap_path = ROOT / entry.get("path", "")
            if not ap_path.exists():
                print(f"real-data-only: missing {entry.get('path')}",
                       file=sys.stderr)
                return 2

    if args.dry_run:
        out = {"mode": "dry-run", "pin_hash": pin_hash,
                "pinned_input_count": len(pinned), "ok": True}
        print(json.dumps(out, indent=2, sort_keys=True) if args.json
              else f"=== dream-curriculum dry-run ===\n{out}")
        return 0

    # Load upstream artifacts under the pin
    self_model = _load_self_model(pinned) or {}
    curiosity_log = _load_curiosity_log(pinned)
    calibration_corrections = _load_calibration_corrections(pinned)

    branch = _detect_branch() or "phase8.5/dream-curriculum"
    base_commit = _detect_base_commit() or ""

    c = build(
        self_model=self_model,
        curiosity_log=curiosity_log,
        calibration_corrections=calibration_corrections,
        branch_name=branch,
        base_commit_hash=base_commit,
        pin_hash=pin_hash,
        top_nights=args.top_nights,
        cell_filter=args.cell,
    )

    out_dir = args.output_dir or _default_out_dir(pin_hash)
    if args.apply:
        paths = emit(c, out_dir)
        if args.json:
            print(json.dumps({k: p.as_posix() for k, p in paths.items()},
                              indent=2))
        else:
            for k, p in paths.items():
                print(f"{k}: {p.as_posix()}")
            print(f"primary_source: {c.primary_source}")
            print(f"nights: {len(c.nights)}")
    else:
        d = cu.curriculum_to_dict(c)
        if args.json:
            print(json.dumps(d, indent=2, sort_keys=True))
        else:
            print(f"=== dream curriculum dry-run ===")
            print(f"primary_source: {c.primary_source}")
            print(f"counts_by_mode: {c.counts_by_mode}")
            print(f"counts_by_kind: {c.counts_by_kind}")
            print(f"nights: {len(c.nights)}")
            print(f"(default output-dir: {_default_out_dir(pin_hash).as_posix()})")
            print("(use --apply to write artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
