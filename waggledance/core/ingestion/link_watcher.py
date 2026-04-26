"""Link watcher — Phase 9 §H.

Detects mutations on linked external sources. The R7.5 pinning rule
applies: append-only growth beyond the pinned size is ALLOWED;
shrinkage / truncation / hash-change within the pinned window is
a CRITICAL FAILURE.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .link_manager import LinkRecord


@dataclass(frozen=True)
class LinkObservation:
    link_id: str
    state: str         # "ok_unchanged" | "ok_growth" | "critical_change" | "missing"
    current_sha256: str | None
    current_size_bytes: int | None
    rationale: str

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "state": self.state,
            "current_sha256": self.current_sha256,
            "current_size_bytes": self.current_size_bytes,
            "rationale": self.rationale,
        }


def observe(link: LinkRecord) -> LinkObservation:
    p = Path(link.external_path)
    if not p.exists():
        return LinkObservation(
            link_id=link.link_id, state="missing",
            current_sha256=None, current_size_bytes=None,
            rationale=f"link target {link.external_path!r} disappeared",
        )
    data = p.read_bytes()
    cur_sha = "sha256:" + hashlib.sha256(data).hexdigest()
    cur_size = len(data)
    if cur_size < link.last_seen_size_bytes:
        return LinkObservation(
            link_id=link.link_id, state="critical_change",
            current_sha256=cur_sha, current_size_bytes=cur_size,
            rationale=(
                f"link source shrank: was {link.last_seen_size_bytes}, "
                f"now {cur_size}"
            ),
        )
    if cur_size == link.last_seen_size_bytes and cur_sha != link.last_seen_sha256:
        return LinkObservation(
            link_id=link.link_id, state="critical_change",
            current_sha256=cur_sha, current_size_bytes=cur_size,
            rationale="link source content changed in-place (same size, different hash)",
        )
    if cur_size > link.last_seen_size_bytes:
        return LinkObservation(
            link_id=link.link_id, state="ok_growth",
            current_sha256=cur_sha, current_size_bytes=cur_size,
            rationale=(
                f"append-only growth: {link.last_seen_size_bytes} → "
                f"{cur_size}"
            ),
        )
    return LinkObservation(
        link_id=link.link_id, state="ok_unchanged",
        current_sha256=cur_sha, current_size_bytes=cur_size,
        rationale="content unchanged",
    )


def observe_all(links: list[LinkRecord]) -> list[LinkObservation]:
    return [observe(l) for l in links]
