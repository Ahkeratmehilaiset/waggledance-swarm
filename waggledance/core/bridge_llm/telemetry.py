# SPDX-License-Identifier: Apache-2.0
"""R20.2 — telemetry log for BridgeLLMClient calls.

One JSONL line per call. Schema fields per R20 master prompt §2.3:
call_id, timestamp, injection_point, provider, model, prompt_hash,
prompt_redaction_status, latency_ms, tokens_in, tokens_out,
fallback_level, success, error_class, cost_cents, budget_state.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import LLMRequest, LLMResponse


class TelemetryLogger:
    """Append-only JSONL writer. Thread-safe."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            root = os.environ.get("AGENT_BRIDGE_RUNTIME_ROOT")
            if root:
                path = Path(root) / "bridge_llm_telemetry.jsonl"
            else:
                path = Path("data") / "bridge_llm" / "telemetry.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def log_call(
        self,
        request: LLMRequest,
        response: LLMResponse,
        budget_state: dict[str, Any] | None = None,
        model: str = "",
    ) -> None:
        prompt_hash = hashlib.sha256(
            request.prompt.encode("utf-8")
        ).hexdigest()[:16]
        record = {
            "call_id": response.call_id or prompt_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "injection_point": request.injection_point,
            "provider": response.provider,
            "model": model,
            "prompt_hash": prompt_hash,
            "prompt_redaction_status": (
                "applied" if response.redaction_applied else "skipped"
            ),
            "latency_ms": round(response.latency_ms, 4),
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "fallback_level": int(response.fallback_level),
            "success": response.success,
            "error_class": response.error_class,
            "cost_cents": response.cost_cents,
            "budget_state": budget_state,
            "speculative": request.speculative,
            "cached": response.cached,
        }
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
