# SPDX-License-Identifier: BUSL-1.1
"""Served-receipt emitter that bridges ChatService to the T3 sink (P2 S1b).

This is the layer ChatService.handle will call at EVERY served return point (the
spec named 4 -- hotcache / solver / hex / final -- but the live handle also serves
from the HYBRID-retrieval path, so there are FIVE; missing one under-counts the
denominator, the #1495 failure mode). It is DORMANT until ChatService calls it.

Two-phase, matching the served_pending contract (rco-1's 5 block-conditions):

* ``record_pending`` -- SYNCHRONOUS, called before the response returns: the
  crash-safe DENOMINATOR. FAIL-OPEN: on any error it notes the failure (bc4:
  surfaced via ``pending_append_failures`` so the claim goes not-eligible, NEVER
  swallowed) and returns False; the caller still serves the user. Windowed fsync
  (bc5) -- the sync path never fsyncs, so serving is not blocked on disk.
* ``schedule_receipt`` -- OFF the event loop, fire-and-forget after the response:
  build the chat-served MAGMA receipt (raw query/response never leave -- only
  digests, via build_chat_served_summary) and resolve the pending to a receipt; on
  any failure resolve it to a GAP (never leave it swallowed). Runs on a worker
  thread so it never blocks the event loop.

Everything is FAIL-OPEN: no method ever raises to the caller. When disabled, all
methods are no-ops. This module does NOT flip claim_safe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from waggledance.core.magma.chat_served_accounting import (
    PENDING_APPEND_FAILURE_REASONS,
    PENDING_APPEND_FAILURE_SCHEMA,
)
from waggledance.core.magma.chat_served_metadata import (
    WORLD_SNAPSHOT_NA_MARKER,
    normalize_agent_id,
    normalize_language,
    normalize_profile,
    normalize_token,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_served_ledger import is_path_safe_token
from waggledance.core.magma.chat_served_receipt import build_chat_served_summary

log = logging.getLogger(__name__)

# ISO-8601 with a trailing Z and no '+' offset, so the ts is a conforming ledger
# token (the '+' in '+00:00' is outside the token charset and would be rejected).
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def new_served_id() -> str:
    """A unique, conforming-token served_id (bc3 uniqueness rests on this)."""
    return uuid.uuid4().hex


class ChatServedEmitter:
    """Fail-open bridge from ChatService served points to the chat-served sink."""

    def __init__(
        self,
        *,
        sink: Any,
        out_dir: Any,
        verify_manifest: Callable[..., Mapping[str, Any]],
        known_profiles: Iterable[str],
        enabled: bool = True,
        now_fn: Callable[[], datetime] = _default_now,
    ) -> None:
        self._sink = sink
        self._out_dir = out_dir
        self._pending_failure_ledger_path = (
            Path(out_dir) / "pending_append_failures.jsonl"
        )
        self._verify_manifest = verify_manifest
        self._known_profiles = frozenset(p for p in known_profiles if isinstance(p, str))
        self._enabled = bool(enabled)
        self._now_fn = now_fn
        self._ordinal = 0
        self._pending_append_failures = 0  # bc4: surfaced so the claim can go not-eligible
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def pending_append_failures(self) -> int:
        """Count of served queries whose pending append FAILED (bc4). While > 0 over
        a window, the claim must be treated as not-eligible: served queries exist
        that the ledger denominator does not contain (an independent, non-swallowed
        surface of the failure)."""
        return self._pending_append_failures

    @property
    def pending_failure_ledger_path(self) -> str:
        return str(self._pending_failure_ledger_path)

    def _metadata(
        self, *, source: str, route_type: str, language: str, profile: str, agent_id: str | None
    ) -> dict[str, str]:
        """Normalize request/route metadata to conforming HONEST tokens (T1) -- raw
        request-controlled values never reach the ledger as-is."""
        metadata = {
            "source": normalize_token(source),
            "route_type": normalize_token(route_type),
            "language": normalize_language(language),
            "profile": normalize_profile(profile, self._known_profiles),
            "world_snapshot_ref": WORLD_SNAPSHOT_NA_MARKER,
        }
        normalized_agent = normalize_agent_id(agent_id)
        if normalized_agent is not None:
            metadata["agent_id"] = normalized_agent
        return metadata

    def record_pending(
        self, served_id: str, *, source: str, route_type: str, language: str,
        profile: str, agent_id: str | None,
    ) -> bool:
        """SYNC crash-safe denominator. FAIL-OPEN: returns True on success, False when
        disabled OR on any error (the error is surfaced via pending_append_failures,
        never swallowed, and the caller still serves the user)."""
        if not self._enabled:
            return False
        if not is_path_safe_token(served_id):
            # INGRESS defense (tools/rco-2 PR#1500): an unsafe served_id must NEVER
            # become a counted receipt -- downstream it is a filesystem path segment,
            # so '/'/'..' would let the receipt escape out_dir yet still read eligible.
            # Surface it (bc4) so the claim goes not-eligible; serve the user anyway.
            self._pending_append_failures += 1
            ts = self._now_fn().strftime(_TS_FORMAT)
            self._write_pending_append_failure(
                served_id,
                ts,
                "metadata_rejected",
                {
                    "source": normalize_token(source),
                    "route_type": normalize_token(route_type),
                    "language": normalize_language(language),
                    "profile": normalize_profile(profile, self._known_profiles),
                },
            )
            log.debug("chat-served pending rejected: served_id not path-safe (serving continues)")
            return False
        try:
            metadata = self._metadata(
                source=source, route_type=route_type, language=language,
                profile=profile, agent_id=agent_id,
            )
            ts = self._now_fn().strftime(_TS_FORMAT)
            self._sink.record_pending(served_id, ts, metadata, fsync=False)  # windowed (bc5)
            return True
        except Exception:  # noqa: BLE001 -- fail-OPEN: serving must never break on this
            self._pending_append_failures += 1  # bc4: surface, do not swallow
            try:
                metadata = self._metadata(
                    source=source, route_type=route_type, language=language,
                    profile=profile, agent_id=agent_id,
                )
            except Exception:  # noqa: BLE001 -- sanitize best-effort without blocking serve
                metadata = {"source": "unknown", "route_type": "unknown"}
            self._write_pending_append_failure(
                served_id,
                self._now_fn().strftime(_TS_FORMAT),
                "sink_write_failed",
                metadata,
            )
            log.debug("chat-served pending append failed (serving continues)", exc_info=True)
            return False

    def _write_pending_append_failure(
        self,
        served_id: str,
        ts_utc: str,
        reason: str,
        metadata: Mapping[str, str],
    ) -> None:
        if reason not in PENDING_APPEND_FAILURE_REASONS:
            reason = "sink_write_failed"
        try:
            path = self._pending_failure_ledger_path
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "schema_version": PENDING_APPEND_FAILURE_SCHEMA,
                "served_id_hash": sha256_digest({"served_id": str(served_id)}),
                "ts_utc": ts_utc,
                "reason": reason,
                "metadata": {
                    str(k): str(v)
                    for k, v in metadata.items()
                    if isinstance(k, str) and isinstance(v, str)
                },
            }
            payload = json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:  # noqa: BLE001 -- fail-open serving still wins
            log.debug(
                "chat-served pending failure ledger append failed",
                exc_info=True,
            )

    def schedule_receipt(
        self, served_id: str, *, query: str, response: str, source: str, route_type: str,
        confidence: float, latency_ms: float, cached: bool, round_table: bool,
        agent_id: str | None, language: str, profile: str,
        route_stage_trace: Any = None,
    ) -> None:
        """Fire-and-forget the OFF-LOOP resolution (never blocks the response). No-op
        when disabled. Any scheduling failure is swallowed at the SCHEDULE layer only
        (a crash then leaves the pending unresolved -> a gap on the next walk)."""
        if not self._enabled:
            return
        if not is_path_safe_token(served_id):
            return  # never build/resolve a receipt for an unsafe served_id (see record_pending)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop (non-async context) -> nothing to schedule
        coro = self._resolve(
            served_id, query=query, response=response, source=source, route_type=route_type,
            confidence=confidence, latency_ms=latency_ms, cached=cached, round_table=round_table,
            agent_id=agent_id, language=language, profile=profile, route_stage_trace=route_stage_trace,
        )
        task = loop.create_task(coro)
        self._tasks.add(task)                       # keep a strong ref so it is not GC'd
        task.add_done_callback(self._tasks.discard)

    async def _resolve(
        self, served_id: str, *, query: str, response: str, source: str, route_type: str,
        confidence: float, latency_ms: float, cached: bool, round_table: bool,
        agent_id: str | None, language: str, profile: str, route_stage_trace: Any,
    ) -> None:
        """Build the receipt off-loop and resolve the pending to receipt|gap. Every
        failure ends the pending as a GAP -- never left swallowed/unresolved by us."""
        now = self._now_fn()
        ts = now.strftime(_TS_FORMAT)
        try:
            self._ordinal += 1
            ordinal = self._ordinal
            summary = build_chat_served_summary(
                query=query, response=response, route_type=route_type, source=source,
                confidence=confidence, latency_ms=latency_ms, cached=cached,
                round_table=round_table, agent_id=agent_id, language=language,
                profile=str(normalize_profile(profile, self._known_profiles)),
                world_snapshot_ref=WORLD_SNAPSHOT_NA_MARKER,
                route_stage_trace=route_stage_trace,
            )
            report = await asyncio.to_thread(self._write_bundle, summary, now, ordinal, served_id)
            receipt_ref = sha256_digest(report["receipt"])
            await asyncio.to_thread(self._sink.resolve_receipt, served_id, ts, receipt_ref)
        except Exception:  # noqa: BLE001 -- resolution failure -> gap, never a silent hole
            log.debug("chat-served receipt resolution failed -> gap", exc_info=True)
            try:
                await asyncio.to_thread(
                    self._sink.resolve_gap, served_id, ts, "receipt_build_failed"
                )
            except Exception:  # noqa: BLE001 -- pending stays unresolved -> gap on walk anyway
                log.debug("chat-served gap resolution also failed", exc_info=True)

    @staticmethod
    def _safe_bundle_name(served_id: str) -> str:
        """A path-segment-safe, collision-free leaf derived from served_id.

        served_id is a conforming TOKEN whose charset allows '/' and '.', which is
        UNSAFE as a path component (a '../' would let the bundle escape out_dir while
        the ledger still reports coverage). Deriving the leaf from a sha256 hex digest
        makes escape impossible for ANY served_id (hex is always a single safe segment)
        while staying unique per served_id.
        """
        import hashlib

        return "served-" + hashlib.sha256(str(served_id).encode("utf-8")).hexdigest()[:32]

    def _write_bundle(
        self, summary: Mapping[str, Any], now: datetime, ordinal: int, served_id: str
    ) -> dict[str, Any]:
        import pathlib

        from waggledance.core.magma.chat_served_receipt import write_chat_served_receipt_bundle

        # write_receipt_bundle REQUIRES a non-existent out_dir (it creates it), so each
        # served receipt gets its own unique leaf; the parent is ensured to exist first.
        # The leaf is HASH-derived so a slash/dot-dot served_id can never escape out_dir.
        base = pathlib.Path(self._out_dir)
        base.mkdir(parents=True, exist_ok=True)
        bundle_dir = base / self._safe_bundle_name(served_id)
        # CONTAINMENT belt (rco-2): the consumer ENFORCES at the path boundary and never
        # trusts the token shape -- so even a future validator gap cannot re-open traversal.
        if not bundle_dir.resolve().is_relative_to(base.resolve()):
            raise ValueError(f"bundle_dir escapes out_dir: {bundle_dir!r}")
        return write_chat_served_receipt_bundle(
            out_dir=bundle_dir, summary_payload=summary, now_utc=now,
            verify_manifest=self._verify_manifest, ordinal=ordinal,
        )


__all__ = ["ChatServedEmitter", "new_served_id"]
