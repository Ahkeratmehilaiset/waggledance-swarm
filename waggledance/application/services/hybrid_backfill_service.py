"""Hybrid backfill service — populates cell-local FAISS from existing trusted content.

Scans validated case trajectories and trusted memory, assigns each to a hex cell,
and mirrors into the corresponding cell-local FAISS index. Idempotent via
a watermark / already-indexed tracking set.

NOT auto-run on boot. Triggered manually via admin API or CLI.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    """Result of a single backfill run."""

    started: float = 0.0
    finished: float = 0.0
    total_scanned: int = 0
    indexed: int = 0
    skipped_duplicate: int = 0
    skipped_no_content: int = 0
    skipped_embed_fail: int = 0
    failed: int = 0
    dry_run: bool = False
    cell_counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started": self.started,
            "finished": self.finished,
            "duration_s": round(self.finished - self.started, 2) if self.finished else 0,
            "total_scanned": self.total_scanned,
            "indexed": self.indexed,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_no_content": self.skipped_no_content,
            "skipped_embed_fail": self.skipped_embed_fail,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "cell_counts": self.cell_counts,
            "errors": self.errors[:20],
        }


class HybridBackfillService:
    """Idempotent backfill of cell-local FAISS indices from trusted content.

    Sources:
      1. Validated case trajectories from SQLiteCaseStore
      2. (Future) Trusted memory records from MemoryRepository

    Tracks already-indexed document IDs to prevent duplicates on rerun.
    """

    def __init__(
        self,
        hybrid_retrieval,
        case_store=None,
        embed_fn=None,
    ):
        self._hybrid = hybrid_retrieval
        self._case_store = case_store
        self._embed_fn = embed_fn
        self._indexed_ids: set = set()
        self._last_result: Optional[BackfillResult] = None
        self._running = False
        self._total_runs = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_result(self) -> Optional[BackfillResult]:
        return self._last_result

    def status(self) -> dict:
        """Return current backfill status."""
        return {
            "running": self._running,
            "total_runs": self._total_runs,
            "indexed_ids_count": len(self._indexed_ids),
            "last_result": self._last_result.to_dict() if self._last_result else None,
        }

    async def run(self, dry_run: bool = False, limit: int = 5000) -> BackfillResult:
        """Execute a backfill run.

        Args:
            dry_run: If True, count what would be indexed but don't write.
            limit: Maximum number of cases to scan per run.

        Returns:
            BackfillResult with counts and per-cell breakdown.
        """
        if self._running:
            result = BackfillResult(dry_run=dry_run)
            result.errors.append("Backfill already in progress")
            return result

        self._running = True
        result = BackfillResult(started=time.time(), dry_run=dry_run)

        try:
            await self._backfill_cases(result, dry_run, limit)
        except Exception as e:
            result.errors.append(f"Backfill error: {e}")
            log.exception("Backfill run failed")
        finally:
            result.finished = time.time()
            self._running = False
            self._last_result = result
            self._total_runs += 1

        log.info(
            "Backfill %s: scanned=%d indexed=%d skipped_dup=%d failed=%d (%.1fs)",
            "dry-run" if dry_run else "real",
            result.total_scanned,
            result.indexed,
            result.skipped_duplicate,
            result.failed,
            result.finished - result.started,
        )
        return result

    async def _backfill_cases(
        self, result: BackfillResult, dry_run: bool, limit: int
    ) -> None:
        """Scan case trajectories and index their content into cell-local FAISS."""
        if not self._case_store:
            result.errors.append("No case store available")
            return

        cases = self._case_store.list_full(limit=limit)
        for case in cases:
            result.total_scanned += 1
            doc_id = self._case_doc_id(case)

            # Skip already indexed
            if doc_id in self._indexed_ids:
                result.skipped_duplicate += 1
                continue

            # Extract indexable content
            content = self._extract_content(case)
            if not content:
                result.skipped_no_content += 1
                continue

            # Determine cell assignment
            intent = case.get("intent", "chat")
            data = case.get("data")
            query = ""
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(data, dict):
                query = data.get("query", data.get("message", ""))
            if not query:
                query = content[:200]

            if dry_run:
                # Count what would be indexed
                assignment = self._hybrid._topology.assign_cell(intent, query)
                cell_id = assignment.cell_id
                result.cell_counts[cell_id] = result.cell_counts.get(cell_id, 0) + 1
                result.indexed += 1
                self._indexed_ids.add(doc_id)
                continue

            # Embed content
            if not self._embed_fn:
                result.skipped_embed_fail += 1
                if not result.errors or "No embed function" not in result.errors[-1]:
                    result.errors.append("No embed function available")
                continue

            try:
                vec = self._embed_fn(content)
                if vec is None:
                    result.skipped_embed_fail += 1
                    continue
            except Exception as e:
                result.skipped_embed_fail += 1
                if len(result.errors) < 5:
                    result.errors.append(f"Embed error: {e}")
                continue

            # Ingest into hybrid retrieval
            try:
                cell_id = await self._hybrid.ingest(
                    doc_id=doc_id,
                    text=content,
                    vector=vec,
                    intent=intent,
                    metadata={"source": "backfill", "case_id": case.get("trajectory_id", "")},
                )
                if cell_id:
                    result.indexed += 1
                    result.cell_counts[cell_id] = result.cell_counts.get(cell_id, 0) + 1
                    self._indexed_ids.add(doc_id)
                else:
                    result.failed += 1
            except Exception as e:
                result.failed += 1
                if len(result.errors) < 5:
                    result.errors.append(f"Ingest error: {e}")

    def _case_doc_id(self, case: dict) -> str:
        """Generate a stable document ID for a case trajectory."""
        tid = case.get("trajectory_id", "")
        if tid:
            return f"backfill_case_{tid}"
        # Fallback: hash content
        content = self._extract_content(case)
        h = hashlib.sha256((content or "").encode()).hexdigest()[:16]
        return f"backfill_hash_{h}"

    def _extract_content(self, case: dict) -> str:
        """Extract indexable text content from a case trajectory."""
        data = case.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return data if data else ""

        if isinstance(data, dict):
            parts = []
            query = data.get("query", data.get("message", ""))
            response = data.get("response", "")
            if query:
                parts.append(query)
            if response:
                parts.append(response)
            return " ".join(parts)

        return ""
