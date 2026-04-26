"""Link manager — Phase 9 §H.

Tracks external sources ingested in link_mode (kept in place; not
copied into WD-managed store). Each link records the external path,
source kind, and last-seen content sha so the watcher can detect
mutations on the originally pinned window.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LinkRecord:
    schema_version: int
    link_id: str
    external_path: str
    source_kind: str
    capsule_context: str
    last_seen_sha256: str
    last_seen_size_bytes: int
    last_seen_iso: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "link_id": self.link_id,
            "external_path": self.external_path,
            "source_kind": self.source_kind,
            "capsule_context": self.capsule_context,
            "last_seen_sha256": self.last_seen_sha256,
            "last_seen_size_bytes": self.last_seen_size_bytes,
            "last_seen_iso": self.last_seen_iso,
        }


def compute_link_id(external_path: str, source_kind: str,
                         capsule_context: str) -> str:
    canonical = json.dumps({
        "external_path": external_path, "source_kind": source_kind,
        "capsule_context": capsule_context,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_link(*, external_path: str, source_kind: str,
                  capsule_context: str = "neutral_v1",
                  last_seen_iso: str = "1970-01-01T00:00:00+00:00",
                  ) -> LinkRecord:
    p = Path(external_path)
    if not p.exists():
        raise FileNotFoundError(p)
    data = p.read_bytes()
    sha = "sha256:" + hashlib.sha256(data).hexdigest()
    return LinkRecord(
        schema_version=1,
        link_id=compute_link_id(external_path, source_kind, capsule_context),
        external_path=external_path,
        source_kind=source_kind,
        capsule_context=capsule_context,
        last_seen_sha256=sha,
        last_seen_size_bytes=len(data),
        last_seen_iso=last_seen_iso,
    )


# ── Atomic persistence (R7.5 §G durability rule) ─────────────────-

def save_links(records: list[LinkRecord], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "links": {r.link_id: r.to_dict()
                   for r in sorted(records, key=lambda x: x.link_id)},
    }
    canonical = json.dumps(payload, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".links.", suffix=".tmp",
    ) as tmp:
        tmp.write(canonical)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def load_links(path: Path | str) -> list[LinkRecord]:
    target = Path(path)
    if not target.exists():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    out: list[LinkRecord] = []
    for d in (data.get("links") or {}).values():
        out.append(LinkRecord(
            schema_version=int(d.get("schema_version") or 1),
            link_id=str(d["link_id"]),
            external_path=str(d["external_path"]),
            source_kind=str(d["source_kind"]),
            capsule_context=str(d.get("capsule_context") or "neutral_v1"),
            last_seen_sha256=str(d["last_seen_sha256"]),
            last_seen_size_bytes=int(d["last_seen_size_bytes"]),
            last_seen_iso=str(d.get("last_seen_iso") or ""),
        ))
    return out
