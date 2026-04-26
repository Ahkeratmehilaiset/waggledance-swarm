"""Presence log — Phase 9 §V.

Append-only chained log of WD's conversational presence events.
Same chain pattern as Session D's history.py and R7.5's vector
event log.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import CONVERSATION_SCHEMA_VERSION, PRESENCE_KINDS

GENESIS_PREV = "0" * 64


@dataclass(frozen=True)
class PresenceEntry:
    schema_version: int
    entry_sha256: str
    ts_iso: str
    kind: str
    summary: str
    prev_entry_sha256: str
    capsule_context: str
    evidence_refs: tuple[str, ...] = ()
    user_turn_excerpt: str | None = None
    wd_turn_excerpt: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in PRESENCE_KINDS:
            raise ValueError(
                f"unknown presence kind: {self.kind!r}; "
                f"allowed: {PRESENCE_KINDS}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "entry_sha256": self.entry_sha256,
            "ts_iso": self.ts_iso,
            "kind": self.kind,
            "summary": self.summary,
            "prev_entry_sha256": self.prev_entry_sha256,
            "capsule_context": self.capsule_context,
            "evidence_refs": list(self.evidence_refs),
            "user_turn_excerpt": self.user_turn_excerpt,
            "wd_turn_excerpt": self.wd_turn_excerpt,
        }


def compute_entry_sha256(entry_dict_without_sha: dict) -> str:
    if "entry_sha256" in entry_dict_without_sha:
        raise ValueError("entry_sha256 must not be in the hash input")
    canonical = json.dumps(entry_dict_without_sha, sort_keys=True,
                              separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_entry(*, ts_iso: str, kind: str, summary: str,
                  prev_entry_sha256: str,
                  capsule_context: str = "neutral_v1",
                  evidence_refs: tuple[str, ...] = (),
                  user_turn_excerpt: str | None = None,
                  wd_turn_excerpt: str | None = None,
                  ) -> PresenceEntry:
    base = {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "ts_iso": ts_iso, "kind": kind, "summary": summary,
        "prev_entry_sha256": prev_entry_sha256,
        "capsule_context": capsule_context,
        "evidence_refs": list(evidence_refs),
        "user_turn_excerpt": user_turn_excerpt,
        "wd_turn_excerpt": wd_turn_excerpt,
    }
    sha = compute_entry_sha256(base)
    return PresenceEntry(
        schema_version=CONVERSATION_SCHEMA_VERSION,
        entry_sha256=sha, ts_iso=ts_iso, kind=kind, summary=summary,
        prev_entry_sha256=prev_entry_sha256,
        capsule_context=capsule_context,
        evidence_refs=tuple(evidence_refs),
        user_turn_excerpt=user_turn_excerpt,
        wd_turn_excerpt=wd_turn_excerpt,
    )


def append_entry(path: Path | str, entry: PresenceEntry) -> Path:
    """Append (skip on duplicate entry_sha256)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_entries(p)
    if any(e.entry_sha256 == entry.entry_sha256 for e in existing):
        return p
    line = json.dumps(entry.to_dict(), sort_keys=True,
                         separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def read_entries(path: Path | str) -> list[PresenceEntry]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[PresenceEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(PresenceEntry(
                schema_version=int(d.get("schema_version") or 1),
                entry_sha256=str(d["entry_sha256"]),
                ts_iso=str(d.get("ts_iso") or ""),
                kind=str(d["kind"]),
                summary=str(d["summary"]),
                prev_entry_sha256=str(d.get("prev_entry_sha256") or GENESIS_PREV),
                capsule_context=str(d.get("capsule_context") or "neutral_v1"),
                evidence_refs=tuple(d.get("evidence_refs") or ()),
                user_turn_excerpt=d.get("user_turn_excerpt"),
                wd_turn_excerpt=d.get("wd_turn_excerpt"),
            ))
        except (KeyError, ValueError):
            continue
    return out


def validate_chain(entries: list[PresenceEntry]) -> tuple[bool, str | None]:
    prev = GENESIS_PREV
    for e in entries:
        if e.prev_entry_sha256 != prev:
            return False, e.entry_sha256
        prev = e.entry_sha256
    return True, None


def latest_prev_entry_sha256(entries: list[PresenceEntry]) -> str:
    return entries[-1].entry_sha256 if entries else GENESIS_PREV
