#!/usr/bin/env python3
"""WaggleDance gap miner — first curiosity organ.

Reads campaign artifacts (hot_results, incident_log, magma hybrid
candidate trace, hex subdivision plan, composition report, cell
manifests), pins the artifact set at session start, and emits a
deterministic curiosity contract: ranked clusters of unresolved or
suspicious query patterns mapped to suspected gap types and
recommended next actions for the teacher loop.

This is **not a frequency report**. The miner separates curiosity
items from raw counts by combining:
  - signal strength (count, fallback rate, latency)
  - structural evidence (cross-cell vocabulary, contradiction
    surface, subdivision pressure)
  - cell-attribution confidence
  - higher-order types (selection helper, bridge composition,
    proposal-template learner, subdivision recommendation, do-
    nothing)

Outputs (all deterministic given a pinned input set):
  - JSON summary at <out_dir>/curiosity_summary.json
  - Markdown report at <out_dir>/curiosity_report.md
  - JSONL event log at <out_dir>/curiosity_log.jsonl
  - per-cell teacher packs at <out_dir>/teacher_packs/<cell>.json

CLI:
  python tools/gap_miner.py --help
  python tools/gap_miner.py                       # discover + dry-run
  python tools/gap_miner.py --apply               # write outputs
  python tools/gap_miner.py --campaign-dir DIR --apply
  python tools/gap_miner.py --fixture tests/fixtures/sample_pin/ --apply
  python tools/gap_miner.py --json
  python tools/gap_miner.py --apply --max-clusters 50

Runtime safety: read-only over disk artifacts. Never opens port 8002,
never imports runtime adapters, never depends on Ollama or live
embeddings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent

# ── Constants ──────────────────────────────────────────────────────

GAP_MINER_SCHEMA_VERSION = 1

CELLS = (
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
)

# Cell-keyword vocabulary mirrored from tools/cell_manifest.py for
# offline determinism. Keep in sync manually; small enough to inline.
_CELL_KEYWORDS: dict[str, list[str]] = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump",
                 "frost", "temperature", "lämpö", "pakkanen", "freezing",
                 "pipe"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "turvallisuus", "varroa", "mite", "swarm"],
    "seasonal": ["season", "month", "winter", "summer", "spring",
                 "autumn", "vuodenaika", "kevät", "kesä", "talvi",
                 "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "colony"],
    "system":   ["system", "status", "health", "uptime", "process",
                 "mtbf", "oee", "diagnose"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],
}

# Gap-type taxonomy from x.txt §A1.8
GAP_TYPES = (
    "missing_solver",            # base solver missing for this query family
    "improvement_opportunity",   # solver exists but slow/unreliable
    "bridge_composition",        # cross-cell composition would help
    "unit_family_mismatch",      # rescaling layer needed
    "contradiction_surface",     # contradictions in current solvers
    "low_confidence_routing",    # router fails to commit to a layer
    "subdivision_pressure",      # cell scope too broad, deserves split
    "meta_solver_opportunity",   # selection / template / scaffolding helper
)

# Recommended next-action taxonomy
NEXT_ACTIONS = (
    "propose_solver",
    "improve_solver",
    "propose_bridge",
    "propose_subdivision",
    "clarify_routing",
    "propose_meta_solver",
    "do_nothing",
)

# Latency thresholds (ms). Above p95 baseline → likely LLM fallback.
LATENCY_FALLBACK_HIGH_MS = 8000
LATENCY_FALLBACK_LOW_MS = 1500

# Cluster size thresholds for evidence_strength
EVIDENCE_HIGH_MIN = 8
EVIDENCE_MEDIUM_MIN = 3


# ── Dataclasses ────────────────────────────────────────────────────

@dataclass(frozen=True)
class PinnedArtifact:
    """One artifact captured at session start. `bytes` and `lines`
    are byte-counts/line-counts read at pin time so two runs against
    the same pin produce byte-identical output."""
    relpath: str
    bytes: int
    lines: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class PinnedSet:
    campaign_dir: str
    artifacts: tuple[PinnedArtifact, ...]
    pinned_at: str            # not deterministic; excluded from hashes
    pin_hash: str             # deterministic — covers paths + bytes + sha


@dataclass(frozen=True)
class CuriosityItem:
    """One curiosity finding. Frozen so consumers cannot mutate the
    contract after emission. Field set is x.txt §A2."""
    curiosity_id: str
    cluster_id: str
    candidate_cell: str | None
    evidence_strength: str            # "low" | "medium" | "high"
    suspected_gap_type: str           # one of GAP_TYPES
    suspected_missing_capability: str | None
    query_examples: tuple[str, ...]
    count: int
    fallback_rate: float | None       # 0..1
    uncertainty_signature: str | None
    latency_signature: dict[str, float | None]   # {"p50": ..., "p95": ...}
    contradiction_hints: tuple[str, ...]
    bridge_candidate_refs: tuple[str, ...]
    subdivision_pressure_hint: float | None
    estimated_value: float            # ranking score
    recommended_next_action: str      # one of NEXT_ACTIONS
    teacher_input_payload: dict[str, Any]
    provenance: dict[str, Any]
    pinned_artifact_root: str
    # continuity_anchor required by x.txt §A2:
    #   {branch_name, base_commit_hash, pinned_artifact_manifest_sha256}
    continuity_anchor: dict[str, str] | None = None


@dataclass(frozen=True)
class GapMinerReport:
    schema_version: int
    pinned_set: PinnedSet
    curiosities: tuple[CuriosityItem, ...]
    counts_by_type: dict[str, int]
    counts_by_action: dict[str, int]
    counts_by_cell: dict[str, int]
    rows_scanned: int
    rows_unresolved: int
    rows_high_latency: int


# ── Utilities ──────────────────────────────────────────────────────

def _sha256_file(path: Path, max_bytes: int | None = None) -> str:
    """sha256 over the file contents up to `max_bytes` (or all)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if max_bytes is None:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
        else:
            remaining = max_bytes
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    return h.hexdigest()


def _stable_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, compact separators, default str."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def _normalize_query(text: str) -> str:
    """Lowercased, alphanumeric-only token stream. Used for clustering."""
    if not text:
        return ""
    text = text.lower()
    # Replace non-alphanumeric with space, collapse whitespace
    text = re.sub(r"[^\w\säöå]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _token_signature(text: str, max_tokens: int = 6) -> str:
    """Stable signature for a clustering key: sorted top-N tokens by
    length (proxy for content) joined by underscore. Deterministic."""
    norm = _normalize_query(text)
    tokens = [t for t in norm.split() if len(t) > 2]
    tokens = sorted(set(tokens), key=lambda t: (-len(t), t))[:max_tokens]
    if not tokens:
        return "_empty_"
    return "_".join(sorted(tokens))


def _query_hash(query: str) -> str:
    """Stable per-query hash used as a cluster member identity.
    sha256 over the normalized query, truncated to 16 hex chars.
    Same logical query always produces the same hash regardless of
    whitespace or punctuation differences."""
    norm = _normalize_query(query)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
    return float(xs[k])


def _attribute_to_cell(query: str) -> str | None:
    q = _normalize_query(query)
    if not q:
        return None
    best_cell = None
    best_score = 0
    for cell, kws in _CELL_KEYWORDS.items():
        if not kws:
            continue
        score = sum(1 for kw in kws if kw in q)
        if score > best_score:
            best_score = score
            best_cell = cell
    return best_cell


# ── Pinning ────────────────────────────────────────────────────────

def _candidate_artifact_paths(campaign_dir: Path) -> list[Path]:
    candidates = [
        campaign_dir / "hot_results.jsonl",
        campaign_dir / "incident_log.jsonl",
        campaign_dir / "campaign_state.json",
        campaign_dir / "query_corpus.json",   # qid → text join table
        ROOT / "docs" / "runs" / "magma_hybrid_candidate_trace.jsonl",
        ROOT / "docs" / "runs" / "hex_subdivision_plan.md",
        ROOT / "docs" / "runs" / "solver_composition_report.md",
        ROOT / "docs" / "cells" / "INDEX.md",
    ]
    return [p for p in candidates if p.exists()]


_QUERY_ID_RE = re.compile(r"^(\d+)(?:_c\d+)?$")


def _parse_query_id(raw: Any) -> int | None:
    """Extract the integer corpus query_id from either int or
    `<id>_c<cycle>` string form."""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        m = _QUERY_ID_RE.match(raw)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _load_query_corpus(pinned: PinnedSet) -> dict[int, str]:
    """Map corpus query_id → query text under the pin. Empty dict if
    corpus missing."""
    found = _pinned_lookup(pinned, "query_corpus.json")
    if found is None:
        return {}
    path, byte_limit, _ = found
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        text = f.read(byte_limit).decode("utf-8", errors="replace")
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[int, str] = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict):
                qid = r.get("query_id")
                q = r.get("query")
                if isinstance(qid, int) and isinstance(q, str):
                    out[qid] = q
    elif isinstance(rows, dict):
        # Allow {qid: text} dict form too
        for k, v in rows.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
    return out


def discover_campaign_dir(root: Path = ROOT) -> Path | None:
    """Latest `docs/runs/ui_gauntlet_400h_*` directory by name."""
    candidates = sorted(
        p for p in (root / "docs" / "runs").glob("ui_gauntlet_400h_*")
        if p.is_dir()
    )
    return candidates[-1] if candidates else None


def pin_artifacts(campaign_dir: Path | None,
                  fixture_dir: Path | None = None,
                  root: Path = ROOT) -> PinnedSet:
    """Capture byte-counts and line-counts for every artifact at the
    moment of pinning. The result is the deterministic input contract
    for this run."""
    if fixture_dir is not None:
        # Fixture mode: pin every JSONL under the fixture dir
        paths = sorted(fixture_dir.rglob("*"))
        paths = [p for p in paths if p.is_file()]
        rel_root = fixture_dir
    else:
        if campaign_dir is None:
            campaign_dir = discover_campaign_dir(root)
        if campaign_dir is None:
            paths = []
            rel_root = root
        else:
            paths = _candidate_artifact_paths(campaign_dir)
            rel_root = root

    pinned: list[PinnedArtifact] = []
    for p in paths:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        line_count: int | None = None
        if p.suffix == ".jsonl":
            line_count = 0
            with open(p, "rb") as f:
                for _ in f:
                    line_count += 1
        sha = _sha256_file(p, max_bytes=sz)
        try:
            relpath = p.relative_to(root).as_posix()
        except ValueError:
            relpath = p.as_posix()
        pinned.append(PinnedArtifact(
            relpath=relpath, bytes=sz, lines=line_count, sha256=sha,
        ))

    payload = [
        {"path": a.relpath, "bytes": a.bytes,
         "lines": a.lines, "sha256": a.sha256}
        for a in pinned
    ]
    pin_hash = "sha256:" + hashlib.sha256(
        _stable_json({"artifacts": payload}).encode("utf-8")
    ).hexdigest()
    pinned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cdir_str = str(campaign_dir.relative_to(root)) if (
        campaign_dir is not None and campaign_dir.is_absolute()
        and str(campaign_dir).startswith(str(root))
    ) else (campaign_dir.as_posix() if campaign_dir else "")

    return PinnedSet(
        campaign_dir=cdir_str.replace("\\", "/"),
        artifacts=tuple(pinned),
        pinned_at=pinned_at,
        pin_hash=pin_hash,
    )


# ── Artifact reading (under pin) ───────────────────────────────────

def _read_jsonl_pinned(path: Path, byte_limit: int,
                       line_limit: int | None) -> Iterable[dict]:
    if not path.exists():
        return
    with open(path, "rb") as f:
        chunk = f.read(byte_limit)
    text = chunk.decode("utf-8", errors="replace")
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line_limit is not None and n >= line_limit:
            break
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # Tolerate per current offline policy
            continue
        n += 1


def _pinned_lookup(pinned: PinnedSet, suffix: str) -> tuple[Path, int, int | None] | None:
    """Find an artifact by path-suffix in the pinned set. Returns
    (absolute_path, byte_limit, line_limit) or None."""
    for a in pinned.artifacts:
        if a.relpath.endswith(suffix):
            ap = ROOT / a.relpath
            return ap, a.bytes, a.lines
    return None


# ── Signal extraction ─────────────────────────────────────────────

@dataclass
class _CandidateSignal:
    """Raw signal from one campaign row, pre-clustering."""
    query: str
    bucket: str
    responded: bool
    latency_ms: float | None
    error: str
    has_route: bool
    route_layer: str | None


def _scan_hot_results(pinned: PinnedSet,
                       corpus: dict[int, str] | None = None) -> list[_CandidateSignal]:
    """Read hot_results.jsonl rows, joining each row's query_id to the
    pinned `query_corpus.json` so we get the actual query text. Rows
    that carry `query` directly are also handled (Stage-0 fixture
    shape) — corpus lookup is best-effort."""
    found = _pinned_lookup(pinned, "hot_results.jsonl")
    if found is None:
        return []
    path, byte_limit, line_limit = found
    if corpus is None:
        corpus = _load_query_corpus(pinned)
    out: list[_CandidateSignal] = []
    for row in _read_jsonl_pinned(path, byte_limit, line_limit):
        # Prefer inline query if present (test fixtures emit this)
        q = (row.get("query") or "").strip() if isinstance(row.get("query"), str) else ""
        if not q:
            qid = _parse_query_id(row.get("query_id"))
            if qid is not None:
                q = corpus.get(qid, "").strip()
        if not q:
            continue
        out.append(_CandidateSignal(
            query=q,
            bucket=row.get("bucket", "?"),
            responded=bool(row.get("responded")),
            latency_ms=row.get("latency_ms") if isinstance(
                row.get("latency_ms"), (int, float)
            ) else None,
            error=str(row.get("error") or "")[:120],
            has_route="route_layer" in row or "route" in row,
            route_layer=row.get("route_layer"),
        ))
    return out


def _scan_incidents(pinned: PinnedSet) -> Counter:
    """Map incident category → count under pin."""
    found = _pinned_lookup(pinned, "incident_log.jsonl")
    if found is None:
        return Counter()
    path, byte_limit, line_limit = found
    cats: Counter = Counter()
    for row in _read_jsonl_pinned(path, byte_limit, line_limit):
        cat = row.get("category", "?")
        cats[cat] += 1
    return cats


def _scan_subdivision_pressure(pinned: PinnedSet) -> dict[str, float]:
    """Pull severity scores from hex_subdivision_plan.md."""
    found = _pinned_lookup(pinned, "hex_subdivision_plan.md")
    if found is None:
        return {}
    path, byte_limit, _ = found
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        text = f.read(byte_limit).decode("utf-8", errors="replace")
    out: dict[str, float] = {}
    # Match "### `<cell>` — severity X.YZ"
    for m in re.finditer(
        r"^###\s+`([a-z_]+)`\s+—\s+severity\s+([0-9.]+)\s*$",
        text, re.MULTILINE,
    ):
        cell, sev = m.group(1), m.group(2)
        try:
            out[cell] = float(sev)
        except ValueError:
            pass
    return out


def _scan_bridge_candidates(pinned: PinnedSet) -> list[dict]:
    """Read top bridge rows from solver_composition_report.md."""
    found = _pinned_lookup(pinned, "solver_composition_report.md")
    if found is None:
        return []
    path, byte_limit, _ = found
    if not path.exists():
        return []
    with open(path, "rb") as f:
        text = f.read(byte_limit).decode("utf-8", errors="replace")
    rows: list[dict] = []
    # Match table rows under "## Top bridge candidates":
    # | <score> | `<from>` | `<to>` | `<unit>` | <path> |
    in_section = False
    for line in text.splitlines():
        if line.strip().startswith("## Top bridge candidates"):
            in_section = True
            continue
        if in_section and line.strip().startswith("##"):
            break
        m = re.match(
            r"^\|\s*([0-9.]+)\s*\|\s*`([a-z_]+)`\s*\|\s*`([a-z_]+)`\s*\|"
            r"\s*`([^`]*)`\s*\|\s*(.*?)\s*\|$",
            line,
        )
        if m:
            try:
                score = float(m.group(1))
            except ValueError:
                continue
            rows.append({
                "score": score,
                "from": m.group(2),
                "to": m.group(3),
                "unit": m.group(4),
                "path": m.group(5).strip(),
            })
    return rows


# ── Clustering ─────────────────────────────────────────────────────

@dataclass
class _Cluster:
    """A group of similar candidate signals before being turned into a
    CuriosityItem. Mutable here; frozen CuriosityItem is built later."""
    signature: str
    queries: list[str] = field(default_factory=list)
    member_query_hashes: set[str] = field(default_factory=set)
    buckets: Counter = field(default_factory=Counter)
    response_count: int = 0
    no_response_count: int = 0
    latencies: list[float] = field(default_factory=list)
    errors: Counter = field(default_factory=Counter)
    cells: Counter = field(default_factory=Counter)

    def cluster_id(self) -> str:
        """Content-derived cluster id per x.txt CLUSTERING DETERMINISM
        RULE: sha256 over the sorted member query_hashes, truncated
        to 12 hex chars. Two clusters with the same set of members
        always get the same id; clustering iteration order does not
        affect the id."""
        sorted_members = sorted(self.member_query_hashes)
        blob = "|".join(sorted_members).encode("utf-8")
        return "cl_" + hashlib.sha256(blob).hexdigest()[:12]


def _cluster_signals(signals: list[_CandidateSignal]) -> list[_Cluster]:
    by_sig: dict[str, _Cluster] = {}
    for s in signals:
        sig = _token_signature(s.query)
        if sig not in by_sig:
            by_sig[sig] = _Cluster(signature=sig)
        c = by_sig[sig]
        c.member_query_hashes.add(_query_hash(s.query))
        if len(c.queries) < 5 and s.query not in c.queries:
            c.queries.append(s.query)
        c.buckets[s.bucket] += 1
        if s.responded:
            c.response_count += 1
        else:
            c.no_response_count += 1
        if s.latency_ms is not None:
            c.latencies.append(s.latency_ms)
        if s.error:
            c.errors[s.error] += 1
        cell = _attribute_to_cell(s.query)
        if cell:
            c.cells[cell] += 1
    # Deterministic ordering of clusters by content-derived cluster_id
    return sorted(by_sig.values(), key=lambda c: c.cluster_id())


# ── Curiosity item construction ───────────────────────────────────

def _decide_gap_type(c: _Cluster,
                       subdivision_pressure: dict[str, float],
                       cluster_sev_hint: float | None) -> str:
    """Heuristic mapping from cluster shape to gap-type taxonomy.

    `cluster_sev_hint` carries the subdivision-pressure signal that
    accounts for both the offline planner's severity (from
    hex_subdivision_plan.md) AND the cluster-density rule (>=3
    missing-solver clusters share the same candidate_cell). When
    that hint is set strongly enough, subdivision_pressure wins.
    """
    n = c.response_count + c.no_response_count
    fallback_rate = c.no_response_count / n if n else 0.0
    p95 = _percentile(c.latencies, 0.95) or 0.0
    most_common_cell = c.cells.most_common(1)[0][0] if c.cells else None

    # Subdivision pressure: either the planner says so OR the
    # cluster-density rule fires.
    if most_common_cell and cluster_sev_hint is not None and cluster_sev_hint >= 3.0:
        return "subdivision_pressure"
    # Mostly unresolved → missing solver
    if fallback_rate >= 0.5:
        return "missing_solver"
    # Resolved but slow → improvement opportunity
    if fallback_rate < 0.3 and p95 >= LATENCY_FALLBACK_HIGH_MS:
        return "improvement_opportunity"
    # Cluster spans multiple cells → bridge composition
    if len(c.cells) >= 2 and fallback_rate < 0.5:
        return "bridge_composition"
    # Errors dominate → contradiction surface
    if sum(c.errors.values()) >= max(1, n // 2):
        return "contradiction_surface"
    # Cell unattributed but signal exists → low_confidence_routing
    if most_common_cell is None and n >= 2:
        return "low_confidence_routing"
    # Buckets diverse → meta-solver opportunity
    if len(c.buckets) >= 3 and n >= EVIDENCE_MEDIUM_MIN:
        return "meta_solver_opportunity"
    return "missing_solver"


def _decide_next_action(gap_type: str) -> str:
    return {
        "missing_solver": "propose_solver",
        "improvement_opportunity": "improve_solver",
        "bridge_composition": "propose_bridge",
        "unit_family_mismatch": "propose_bridge",
        "contradiction_surface": "clarify_routing",
        "low_confidence_routing": "clarify_routing",
        "subdivision_pressure": "propose_subdivision",
        "meta_solver_opportunity": "propose_meta_solver",
    }.get(gap_type, "do_nothing")


def _evidence_strength_label(count: int) -> str:
    if count >= EVIDENCE_HIGH_MIN:
        return "high"
    if count >= EVIDENCE_MEDIUM_MIN:
        return "medium"
    return "low"


# Normalized evidence-strength weights for the expected_value formula.
# These are the [0,1] scalars referenced by the spec.
_EVIDENCE_WEIGHT = {"low": 0.3, "medium": 0.6, "high": 1.0}


def _evidence_strength_value(label: str) -> float:
    return _EVIDENCE_WEIGHT.get(label, 0.0)


def _estimated_value(c: _Cluster, gap_type: str,
                       has_bridge_refs: bool) -> float:
    """Spec-mandated ranking formula (x.txt EXPECTED VALUE FORMULA):

        expected_value =
            count
            × fallback_rate
            × evidence_strength
            × (1 + bridge_candidate_bonus)

    where:
      count            = cluster case count
      fallback_rate    = no_response / total in [0,1]
      evidence_strength = normalized weight in [0,1]
                          ({"low":0.3, "medium":0.6, "high":1.0})
      bridge_candidate_bonus = 0.25 if any bridge refs else 0.0

    Note: a cluster with fallback_rate=0 ranks at 0 under this
    formula. That is intended — a cluster that always succeeds is
    not a curiosity, no matter how big. Boosters for high-latency
    or subdivision-pressure cases are applied via gap_type
    classification (which routes to ACCEPT-class actions) rather
    than by inflating the value here.
    """
    n = c.response_count + c.no_response_count
    if n == 0:
        return 0.0
    fallback_rate = c.no_response_count / n
    evidence_label = _evidence_strength_label(n)
    evidence_value = _evidence_strength_value(evidence_label)
    bridge_bonus = 0.25 if has_bridge_refs else 0.0
    raw = float(n) * fallback_rate * evidence_value * (1.0 + bridge_bonus)
    return round(raw, 4)


def _curiosity_id(cluster_signature: str, gap_type: str,
                   pin_hash: str) -> str:
    blob = f"{cluster_signature}|{gap_type}|{pin_hash}"
    return "cur_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _build_curiosity_item(c: _Cluster,
                           subdivision_pressure: dict[str, float],
                           bridges: list[dict],
                           pinned: PinnedSet,
                           clusters_per_cell: dict[str, int],
                           continuity_anchor_template: dict) -> CuriosityItem:
    n = c.response_count + c.no_response_count
    most_common_cell = c.cells.most_common(1)[0][0] if c.cells else None
    fallback_rate = (c.no_response_count / n) if n else None
    p50 = _percentile(c.latencies, 0.50)
    p95 = _percentile(c.latencies, 0.95)

    # Bridge candidate refs: any bridges whose endpoints touch a cell
    # in this cluster.
    cluster_cells = set(c.cells.keys())
    bridge_refs = tuple(sorted({
        f"{b['from']}->{b['to']}" for b in bridges
        if b["from"] in cluster_cells or b["to"] in cluster_cells
    }))

    # Subdivision-pressure hint per x.txt HIGHER-ORDER CURIOSITY RULE.
    # Two sources contribute:
    # (1) hex_subdivision_plan.md severity for this cell (offline
    #     planner's verdict)
    # (2) cluster-density rule: >= 3 missing-solver clusters share
    #     the same candidate_cell → the cell may be too broad.
    sev_severity = subdivision_pressure.get(most_common_cell or "")
    cluster_density = clusters_per_cell.get(most_common_cell or "", 0)
    sev_hint: float | None
    if sev_severity is not None and sev_severity > 0:
        sev_hint = sev_severity
    elif cluster_density >= 3:
        sev_hint = float(cluster_density)
    else:
        sev_hint = None

    gap_type = _decide_gap_type(c, subdivision_pressure, sev_hint)

    contradiction_hints = tuple(
        sorted(err for err, _ in c.errors.most_common(3))
    )

    teacher_payload = _build_teacher_input_payload(c, gap_type, most_common_cell)

    next_action = _decide_next_action(gap_type)

    evidence_label = _evidence_strength_label(n)
    if evidence_label == "low" and gap_type != "subdivision_pressure":
        next_action = "do_nothing"

    cluster_id = c.cluster_id()
    has_bridge_refs = bool(bridge_refs)

    return CuriosityItem(
        curiosity_id=_curiosity_id(c.signature, gap_type, pinned.pin_hash),
        cluster_id=cluster_id,
        candidate_cell=most_common_cell,
        evidence_strength=evidence_label,
        suspected_gap_type=gap_type,
        suspected_missing_capability=_capability_hint(c, gap_type),
        query_examples=tuple(sorted(c.queries))[:5],
        count=n,
        fallback_rate=round(fallback_rate, 4) if fallback_rate is not None else None,
        uncertainty_signature=None,
        latency_signature={"p50": p50, "p95": p95},
        contradiction_hints=contradiction_hints,
        bridge_candidate_refs=bridge_refs,
        subdivision_pressure_hint=sev_hint,
        estimated_value=_estimated_value(c, gap_type, has_bridge_refs),
        recommended_next_action=next_action,
        teacher_input_payload=teacher_payload,
        provenance={
            "miner_version": GAP_MINER_SCHEMA_VERSION,
            "cluster_signature": c.signature,
            "buckets": dict(c.buckets),
            "clusters_in_cell": cluster_density,
        },
        pinned_artifact_root=pinned.campaign_dir,
        continuity_anchor=dict(continuity_anchor_template),
    )


def _capability_hint(c: _Cluster, gap_type: str) -> str | None:
    """Free-text hint of what capability seems missing. Deterministic:
    derived from token-ngrams, not model output."""
    if not c.queries:
        return None
    joined = " ".join(c.queries[:3]).lower()
    tokens = [t for t in re.findall(r"[a-zäöå]{4,}", joined)]
    top = Counter(tokens).most_common(3)
    if not top:
        return None
    return f"capability around: {' / '.join(t for t, _ in top)} ({gap_type})"


def _build_teacher_input_payload(c: _Cluster, gap_type: str,
                                   cell: str | None) -> dict:
    """Compact payload the teacher loop can consume directly. Fixed
    field order for byte-stable serialization."""
    return {
        "gap_type": gap_type,
        "candidate_cell": cell,
        "examples": list(sorted(c.queries))[:5],
        "buckets": dict(c.buckets),
        "evidence_count": c.response_count + c.no_response_count,
        "fallback_count": c.no_response_count,
    }


# ── Top-level mining ──────────────────────────────────────────────

def _build_continuity_anchor(pinned: PinnedSet,
                                branch_name: str | None = None,
                                base_commit_hash: str | None = None) -> dict[str, str]:
    """Per x.txt §A2: continuity_anchor must include branch_name,
    base_commit_hash, and pinned_artifact_manifest_sha256."""
    return {
        "branch_name": branch_name or _detect_branch() or "",
        "base_commit_hash": base_commit_hash or _detect_base_commit() or "",
        "pinned_artifact_manifest_sha256": pinned.pin_hash,
    }


def _detect_branch() -> str | None:
    """Best-effort: read git HEAD ref. Returns None if .git is
    missing or unreadable."""
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return None
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("ref: "):
        return text[5:].strip().rsplit("/", 1)[-1]
    return None


def _detect_base_commit() -> str | None:
    """Best-effort: resolve current HEAD commit. Returns None if
    .git is missing or in a detached state we can't follow."""
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return None
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("ref: "):
        ref_path = ROOT / ".git" / text[5:].strip()
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
        # Could be a packed ref; not worth resolving here
        return None
    # Detached HEAD
    return text or None


def mine(campaign_dir: Path | None = None,
         fixture_dir: Path | None = None,
         max_clusters: int = 200,
         pinned: PinnedSet | None = None,
         branch_name: str | None = None,
         base_commit_hash: str | None = None,
         min_evidence: float = 0.0,
         cell_filter: str | None = None) -> GapMinerReport:
    """End-to-end mine pass. Pure function — does not write files.

    If `pinned` is supplied, the caller has already pinned artifacts
    (e.g. from --artifact-manifest); the function honours that pin
    instead of re-globbing. This is the preferred path for
    reproducible runs.
    """
    if pinned is None:
        pinned = pin_artifacts(campaign_dir, fixture_dir=fixture_dir)
    signals = _scan_hot_results(pinned)
    subdivision_pressure = _scan_subdivision_pressure(pinned)
    bridges = _scan_bridge_candidates(pinned)
    clusters = _cluster_signals(signals)

    # Pre-pass: count missing-solver clusters per cell so the
    # subdivision-pressure heuristic can fire (x.txt HIGHER-ORDER
    # CURIOSITY RULE).
    clusters_per_cell: dict[str, int] = {}
    for c in clusters:
        n = c.response_count + c.no_response_count
        if not n:
            continue
        fr = c.no_response_count / n
        cell = c.cells.most_common(1)[0][0] if c.cells else None
        if fr >= 0.5 and cell:
            clusters_per_cell[cell] = clusters_per_cell.get(cell, 0) + 1

    anchor_template = _build_continuity_anchor(
        pinned, branch_name=branch_name, base_commit_hash=base_commit_hash,
    )

    # Build items.
    items: list[CuriosityItem] = []
    for c in clusters:
        items.append(_build_curiosity_item(
            c, subdivision_pressure, bridges, pinned,
            clusters_per_cell, anchor_template,
        ))

    # Apply CLI filters before ranking
    if cell_filter is not None:
        items = [i for i in items if i.candidate_cell == cell_filter]
    if min_evidence > 0.0:
        items = [i for i in items if i.estimated_value >= min_evidence]

    items.sort(key=lambda i: (-i.estimated_value, i.cluster_id))
    items = items[:max_clusters]

    counts_by_type = Counter(i.suspected_gap_type for i in items)
    counts_by_action = Counter(i.recommended_next_action for i in items)
    counts_by_cell = Counter(i.candidate_cell or "_unattributed" for i in items)

    rows_unresolved = sum(
        1 for s in signals if not s.responded
    )
    rows_high_latency = sum(
        1 for s in signals
        if s.latency_ms is not None and s.latency_ms >= LATENCY_FALLBACK_HIGH_MS
    )

    return GapMinerReport(
        schema_version=GAP_MINER_SCHEMA_VERSION,
        pinned_set=pinned,
        curiosities=tuple(items),
        counts_by_type=dict(sorted(counts_by_type.items())),
        counts_by_action=dict(sorted(counts_by_action.items())),
        counts_by_cell=dict(sorted(counts_by_cell.items())),
        rows_scanned=len(signals),
        rows_unresolved=rows_unresolved,
        rows_high_latency=rows_high_latency,
    )


# ── Emission (deterministic) ──────────────────────────────────────

def _to_summary_dict(report: GapMinerReport) -> dict:
    """Deterministic JSON summary (excludes timestamps)."""
    return {
        "schema_version": report.schema_version,
        "pin_hash": report.pinned_set.pin_hash,
        "campaign_dir": report.pinned_set.campaign_dir,
        "rows_scanned": report.rows_scanned,
        "rows_unresolved": report.rows_unresolved,
        "rows_high_latency": report.rows_high_latency,
        "counts_by_type": report.counts_by_type,
        "counts_by_action": report.counts_by_action,
        "counts_by_cell": report.counts_by_cell,
        "curiosity_count": len(report.curiosities),
        "top_curiosities": [
            {
                "curiosity_id": c.curiosity_id,
                "candidate_cell": c.candidate_cell,
                "gap_type": c.suspected_gap_type,
                "evidence_strength": c.evidence_strength,
                "estimated_value": c.estimated_value,
                "count": c.count,
                "recommended_next_action": c.recommended_next_action,
            }
            for c in report.curiosities[:20]
        ],
    }


def _to_jsonl_lines(report: GapMinerReport) -> list[str]:
    """Stable JSONL output. One line per CuriosityItem in ranked order."""
    lines: list[str] = []
    for c in report.curiosities:
        d = asdict(c)
        # tuples become lists in asdict — ensure stable serialization
        d["query_examples"] = list(d["query_examples"])
        d["contradiction_hints"] = list(d["contradiction_hints"])
        d["bridge_candidate_refs"] = list(d["bridge_candidate_refs"])
        lines.append(_stable_json(d))
    return lines


def _to_markdown(report: GapMinerReport) -> str:
    p = report.pinned_set
    lines = [
        "# Gap miner — curiosity report",
        "",
        f"- **Schema version:** {report.schema_version}",
        f"- **Campaign dir:** `{p.campaign_dir}`",
        f"- **Pin hash:** `{p.pin_hash}`",
        f"- **Rows scanned:** {report.rows_scanned}",
        f"- **Rows unresolved:** {report.rows_unresolved}",
        f"- **Rows high-latency:** {report.rows_high_latency}",
        f"- **Curiosity items:** {len(report.curiosities)}",
        "",
        "## Counts by gap type",
        "",
        "| gap_type | count |",
        "|---|---|",
    ]
    for k, v in report.counts_by_type.items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend(["", "## Counts by recommended action", "",
                   "| action | count |", "|---|---|"])
    for k, v in report.counts_by_action.items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend(["", "## Counts by candidate cell", "",
                   "| cell | count |", "|---|---|"])
    for k, v in report.counts_by_cell.items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend([
        "",
        "## Top curiosity items (by estimated value)",
        "",
        "| rank | curiosity_id | cell | gap_type | evidence | value | action |",
        "|---|---|---|---|---|---|---|",
    ])
    for i, c in enumerate(report.curiosities[:20], start=1):
        lines.append(
            f"| {i} | `{c.curiosity_id}` | `{c.candidate_cell or '—'}` | "
            f"`{c.suspected_gap_type}` | {c.evidence_strength} | "
            f"{c.estimated_value} | `{c.recommended_next_action}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _to_teacher_packs(report: GapMinerReport) -> dict[str, dict]:
    """Group curiosities by candidate_cell into teacher-ready packs."""
    by_cell: dict[str, list[CuriosityItem]] = defaultdict(list)
    for c in report.curiosities:
        cell = c.candidate_cell or "_unattributed"
        by_cell[cell].append(c)

    packs: dict[str, dict] = {}
    for cell, items in by_cell.items():
        pack = {
            "schema_version": report.schema_version,
            "cell_id": cell,
            "pin_hash": report.pinned_set.pin_hash,
            "items": [
                {
                    "curiosity_id": c.curiosity_id,
                    "gap_type": c.suspected_gap_type,
                    "evidence_strength": c.evidence_strength,
                    "estimated_value": c.estimated_value,
                    "count": c.count,
                    "fallback_rate": c.fallback_rate,
                    "latency_p50": c.latency_signature.get("p50"),
                    "latency_p95": c.latency_signature.get("p95"),
                    "query_examples": list(c.query_examples),
                    "recommended_next_action": c.recommended_next_action,
                    "teacher_input_payload": c.teacher_input_payload,
                    "bridge_candidate_refs": list(c.bridge_candidate_refs),
                    "subdivision_pressure_hint": c.subdivision_pressure_hint,
                }
                for c in items
            ],
            "count": len(items),
        }
        packs[cell] = pack
    return packs


def emit(report: GapMinerReport, out_dir: Path) -> dict[str, Path]:
    """Write all four artifact families. Returns a dict of paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "curiosity_summary.json"
    jsonl_path = out_dir / "curiosity_log.jsonl"
    md_path = out_dir / "curiosity_report.md"
    packs_dir = out_dir / "teacher_packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    summary_path.write_text(
        json.dumps(_to_summary_dict(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    jsonl_path.write_text(
        "\n".join(_to_jsonl_lines(report)) + ("\n" if report.curiosities else ""),
        encoding="utf-8",
    )
    md_path.write_text(_to_markdown(report), encoding="utf-8")

    packs = _to_teacher_packs(report)
    for cell, pack in sorted(packs.items()):
        (packs_dir / f"{cell}.json").write_text(
            json.dumps(pack, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return {
        "summary": summary_path,
        "jsonl": jsonl_path,
        "markdown": md_path,
        "teacher_packs": packs_dir,
    }


# ── CLI ────────────────────────────────────────────────────────────

DEFAULT_OUT_DIR = ROOT / "docs" / "runs" / "gap_miner"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", type=Path, default=None,
                    help="campaign artifact root (default: latest "
                         "ui_gauntlet_400h_*)")
    ap.add_argument("--fixture", type=Path, default=None,
                    help="run against a fixture tree under tests/fixtures/")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--max-clusters", type=int, default=200)
    ap.add_argument("--apply", action="store_true",
                    help="write artifacts (default: dry-run summary)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = mine(
        campaign_dir=args.campaign_dir,
        fixture_dir=args.fixture,
        max_clusters=args.max_clusters,
    )

    if args.apply:
        out = emit(report, args.out_dir)
        if args.json:
            print(json.dumps({
                "summary": out["summary"].as_posix(),
                "jsonl": out["jsonl"].as_posix(),
                "markdown": out["markdown"].as_posix(),
                "teacher_packs": out["teacher_packs"].as_posix(),
                "curiosity_count": len(report.curiosities),
                "pin_hash": report.pinned_set.pin_hash,
            }, indent=2))
        else:
            print(f"summary:        {out['summary'].as_posix()}")
            print(f"jsonl:          {out['jsonl'].as_posix()}")
            print(f"markdown:       {out['markdown'].as_posix()}")
            print(f"teacher packs:  {out['teacher_packs'].as_posix()}")
            print(f"curiosity items: {len(report.curiosities)}")
            print(f"pin_hash:       {report.pinned_set.pin_hash}")
    else:
        if args.json:
            print(json.dumps(_to_summary_dict(report), indent=2,
                              sort_keys=True))
        else:
            print(f"=== Gap miner dry-run ===")
            print(f"campaign_dir:    {report.pinned_set.campaign_dir}")
            print(f"pin_hash:        {report.pinned_set.pin_hash}")
            print(f"rows_scanned:    {report.rows_scanned}")
            print(f"rows_unresolved: {report.rows_unresolved}")
            print(f"rows_high_lat:   {report.rows_high_latency}")
            print(f"curiosity items: {len(report.curiosities)}")
            print(f"by gap_type:     {report.counts_by_type}")
            print(f"by action:       {report.counts_by_action}")
            print(f"by cell:         {report.counts_by_cell}")
            print()
            print("(use --apply to write artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
