# SPDX-License-Identifier: BUSL-1.1
"""Optional recorder for sanitized ChatService served evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any

from waggledance.core.magma.chat_served_claim_window_evidence import (
    build_chat_served_claim_window_evidence,
    evidence_digest,
)


class ChatServedEvidenceEmitter:
    """Build sanitized served evidence and pass it to an optional sink."""

    def __init__(
        self,
        sink: Callable[[Mapping[str, Any]], object] | None = None,
    ) -> None:
        self._sink = sink
        self._lock = Lock()

    @property
    def sink_configured(self) -> bool:
        return self._sink is not None

    def emit(
        self,
        *,
        query: str,
        language: str,
        profile: str,
        source: str,
        confidence: float,
        latency_ms: float,
        cached: bool,
        round_table: bool,
        route_stage_trace: Sequence[Mapping[str, Any]] | None,
        served_at_utc: datetime | None = None,
    ) -> dict[str, Any]:
        evidence = build_chat_served_claim_window_evidence(
            query=query,
            language=language,
            profile=profile,
            source=source,
            confidence=confidence,
            latency_ms=latency_ms,
            cached=cached,
            round_table=round_table,
            route_stage_trace=route_stage_trace,
            served_at_utc=served_at_utc,
        )
        digest = evidence_digest(evidence)
        if self._sink is None:
            return _public_report(
                emitted=False,
                sink_configured=False,
                evidence_digest_value=digest,
            )

        try:
            with self._lock:
                self._sink(evidence)
        except Exception as exc:  # noqa: BLE001 - telemetry must not break chat.
            return _public_report(
                emitted=False,
                sink_configured=True,
                evidence_digest_value=digest,
                error=_public_error(exc),
            )
        return _public_report(
            emitted=True,
            sink_configured=True,
            evidence_digest_value=digest,
        )


class JsonlChatServedEvidenceSink:
    """Append sanitized served evidence records to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = Lock()

    def __call__(self, evidence: Mapping[str, Any]) -> dict[str, bool]:
        line = json.dumps(dict(evidence), sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
        return {"written": True, "paths_returned": False, "payloads_returned": False}


def _public_report(
    *,
    emitted: bool,
    sink_configured: bool,
    evidence_digest_value: str,
    error: str | None = None,
) -> dict[str, Any]:
    report = {
        "emitted": bool(emitted),
        "sink_configured": bool(sink_configured),
        "evidence_digest": evidence_digest_value,
        "paths_returned": False,
        "payloads_returned": False,
        "default_chat_served_evidence_emission_changed": False,
        "runtime_authority_changed": False,
    }
    if error is not None:
        report["error"] = error
    return report


def _public_error(exc: BaseException) -> str:
    digest = hashlib.sha256(
        f"{type(exc).__name__}:{exc}".encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    return f"sink_error:{digest}"
