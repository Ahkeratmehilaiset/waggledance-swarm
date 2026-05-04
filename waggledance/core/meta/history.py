"""Meta-proposal history chain — Phase 8.5 Session D, deliverable D6.

docs/runs/hive/HISTORY.jsonl is append-only and unbounded. Each entry
records one (run × meta_proposal_id) tuple plus a chain link
(prev_entry_sha256). Reads tolerate malformed lines (silent skip,
mirroring the R7.5 vector_events policy).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

GENESIS_PREV = "0" * 64


@dataclass(frozen=True)
class HistoryEntry:
    schema_version: int
    meta_proposal_id: str
    proposal_type: str
    output_dir: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    prev_entry_sha256: str
    entry_sha256: str
    ts: str   # ISO 8601, seconds (provided by caller for determinism)


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)


def compute_entry_sha256(entry_dict_without_sha: dict) -> str:
    if "entry_sha256" in entry_dict_without_sha:
        raise ValueError("entry_sha256 must not be in the hash input")
    return hashlib.sha256(
        _canonical(entry_dict_without_sha).encode("utf-8")
    ).hexdigest()


def make_entry(*, meta_proposal_id: str, proposal_type: str,
                  output_dir: str, base_commit_hash: str,
                  pinned_input_manifest_sha256: str,
                  prev_entry_sha256: str, ts: str,
                  schema_version: int = 1) -> HistoryEntry:
    base = {
        "schema_version": schema_version,
        "meta_proposal_id": meta_proposal_id,
        "proposal_type": proposal_type,
        "output_dir": output_dir,
        "base_commit_hash": base_commit_hash,
        "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
        "prev_entry_sha256": prev_entry_sha256,
        "ts": ts,
    }
    sha = compute_entry_sha256(base)
    return HistoryEntry(
        schema_version=schema_version,
        meta_proposal_id=meta_proposal_id,
        proposal_type=proposal_type,
        output_dir=output_dir,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        prev_entry_sha256=prev_entry_sha256,
        entry_sha256=sha,
        ts=ts,
    )


def entry_to_dict(e: HistoryEntry) -> dict:
    return {
        "schema_version": e.schema_version,
        "meta_proposal_id": e.meta_proposal_id,
        "proposal_type": e.proposal_type,
        "output_dir": e.output_dir,
        "base_commit_hash": e.base_commit_hash,
        "pinned_input_manifest_sha256": e.pinned_input_manifest_sha256,
        "prev_entry_sha256": e.prev_entry_sha256,
        "entry_sha256": e.entry_sha256,
        "ts": e.ts,
    }


def read_entries(path: Path | str) -> list[HistoryEntry]:
    p = Path(path)
    out: list[HistoryEntry] = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(HistoryEntry(
                schema_version=int(d.get("schema_version") or 1),
                meta_proposal_id=str(d["meta_proposal_id"]),
                proposal_type=str(d["proposal_type"]),
                output_dir=str(d["output_dir"]),
                base_commit_hash=str(d["base_commit_hash"]),
                pinned_input_manifest_sha256=str(
                    d["pinned_input_manifest_sha256"]
                ),
                prev_entry_sha256=str(d["prev_entry_sha256"]),
                entry_sha256=str(d["entry_sha256"]),
                ts=str(d.get("ts", "")),
            ))
        except (KeyError, ValueError):
            continue
    return out


def append_entry(path: Path | str, entry: HistoryEntry) -> Path:
    """Append-only. Skips silently if an entry with the same
    entry_sha256 already exists in the file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_entries(p)
    if any(e.entry_sha256 == entry.entry_sha256 for e in existing):
        return p   # duplicate skip
    line = _canonical(entry_to_dict(entry))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def validate_chain(entries: list[HistoryEntry]) -> tuple[bool, str | None]:
    """Walk the chain and return (ok, first_break_id_or_None)."""
    prev = GENESIS_PREV
    for e in entries:
        if e.prev_entry_sha256 != prev:
            return False, e.entry_sha256
        prev = e.entry_sha256
    return True, None


def latest_immediate_prev_run_ids(entries: list[HistoryEntry]) -> set[str]:
    """Return the meta_proposal_ids that appeared in the most recent
    *run* (one or more entries sharing the same output_dir)."""
    if not entries:
        return set()
    last_dir = entries[-1].output_dir
    return {e.meta_proposal_id for e in entries
             if e.output_dir == last_dir}


def all_seen_ids(entries: list[HistoryEntry]) -> set[str]:
    return {e.meta_proposal_id for e in entries}


def latest_prev_entry_sha256(entries: list[HistoryEntry]) -> str:
    return entries[-1].entry_sha256 if entries else GENESIS_PREV
