# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""DivergenceAnalyzer v1 -- score candidate-vs-baseline output deltas.

Measurement layer for the Shadow -> Hybrid -> Autonomous migration.
Compares candidate-solver output against operator-baseline output and
produces a structured DivergenceArtifact that the INST-G09 acceptance
gate consumes.

Five format comparators: json, csv, sql_diff, filesystem, text.
Seven template-family severity rule tables (SOLV-001..007).
Conservative default for unknown template families: everything
material until proven noise (spec edit E8).

Privacy: when sensitive_class >= restricted, raw field values are
NEVER stored. Value hashes (sha256 16-hex) + field paths + diff
class + severity + redacted one-line justification only.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/divergence_analyzer_spec.md
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, NamedTuple, Optional


# --------------------------------------------------------------------------
# ComparisonResult -- comparator return type with real field counts
# --------------------------------------------------------------------------


class ComparisonResult(NamedTuple):
    """One comparator's structured result.

    Per Codex RCO round-2 fix: comparators must report real counts of
    total comparable fields and matching fields, not placeholder values.
    INST-G09 + audit consumers treat these as measurement evidence.
    """

    details: list                                 # list[DivergenceDetail]
    n_compared: int                               # total comparable units
    n_matching: int                               # of those, equal in both


# --------------------------------------------------------------------------
# Output schema
# --------------------------------------------------------------------------


class DivergenceCategory(str, Enum):
    IDENTICAL = "identical"
    NEAR_MATCH = "near_match"
    PARTIAL_MATCH = "partial_match"
    DIVERGENT = "divergent"
    INCOMPARABLE = "incomparable"


class DiffClass(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    REORDERED = "reordered"
    TYPE_CHANGED = "type_changed"
    INCOMPARABLE_INPUT = "incomparable_input"


class Severity(str, Enum):
    NOISE = "noise"
    MINOR = "minor"
    MATERIAL = "material"
    CRITICAL = "critical"


@dataclass
class DivergenceScore:
    """Aggregate score for one candidate-vs-baseline comparison."""

    score: float                                  # 0.0 identical .. 1.0 max
    category: str                                 # DivergenceCategory value
    n_fields_compared: int
    n_fields_matching: int
    n_fields_diverging: int


@dataclass
class DivergenceDetail:
    """One field-level diff entry."""

    field_path: str
    candidate_value_hash: str                     # sha256 16-hex; never raw
    baseline_value_hash: str
    diff_class: str                               # DiffClass value
    severity: str                                 # Severity value
    justification: str                            # redacted one-line


@dataclass
class DivergenceArtifact:
    """Full result of one comparison."""

    artifact_id: str
    candidate_output_uri: str
    baseline_output_uri: str
    score: DivergenceScore
    details: list[DivergenceDetail]
    template_family: str
    operator_review_required: bool
    audit_event_id: str
    delta_summary_ref: Optional[str] = None       # detail-events chunk ref


# --------------------------------------------------------------------------
# Severity rules per template family (spec edit E8: unknown -> conservative)
# --------------------------------------------------------------------------


SEVERITY_RULES: dict[str, dict[str, str]] = {
    "RecordReconciler": {
        "timestamp_within_1min": "noise",
        "amount_differing": "material",
        "actor_changed": "critical",
        "row_count_differing": "critical",
        "reordered": "noise",
    },
    "DocumentMiner": {
        "rank_swap_top10": "minor",
        "missing_top10_result": "material",
        "added_result_not_in_baseline": "minor",
        "different_summary_text": "noise",
    },
    "OfferComparator": {
        "score_differing_within_5pct": "noise",
        "ranking_changed_within_top3": "material",
        "missing_offer": "critical",
    },
    "ReportGenerator": {
        "formatting_only": "noise",
        "numeric_value_changed": "material",
        "missing_section": "critical",
    },
    "ScheduledIncrementalSync": {
        "row_count_within_overlap_window": "noise",
        "row_count_outside_overlap": "material",
        "missing_required_field": "critical",
    },
    "PredictiveAnalyzer": {
        "forecast_within_confidence_interval": "noise",
        "forecast_outside_ci": "material",
        "different_input_range": "critical",
    },
    "CrossReferencer": {
        "link_added_high_confidence": "minor",
        "link_removed_high_confidence": "material",
        "false_link_introduced": "critical",
    },
}


def _severity_for(template_family: str, diff_class: str,
                   reason_key: Optional[str] = None,
                   default_unknown: str = "material") -> str:
    """Look up severity for a given (template_family, reason_key).

    Per spec edit E8: unknown_template_family or unknown reason_key
    defaults to material (conservative).
    """
    rules = SEVERITY_RULES.get(template_family)
    if rules is None:
        return default_unknown
    if reason_key and reason_key in rules:
        return rules[reason_key]
    # Diff-class fallback for unknown reason key
    if diff_class in (DiffClass.REMOVED.value, DiffClass.TYPE_CHANGED.value):
        return Severity.CRITICAL.value
    if diff_class == DiffClass.ADDED.value:
        return Severity.MINOR.value
    return default_unknown


# --------------------------------------------------------------------------
# Score thresholds + category resolution
# --------------------------------------------------------------------------


DEFAULT_THRESHOLDS = {
    "near_match": 0.05,
    "partial_match_noise_only": 0.15,
    "partial_match_material": 0.40,
}


def _category_for(score: float, *, has_material: bool, has_critical: bool,
                   incomparable: bool, thresholds: dict[str, float]) -> str:
    if incomparable:
        return DivergenceCategory.INCOMPARABLE.value
    if score == 0.0 and not has_material and not has_critical:
        return DivergenceCategory.IDENTICAL.value
    if has_critical or score >= thresholds.get("partial_match_material",
                                                  DEFAULT_THRESHOLDS["partial_match_material"]):
        return DivergenceCategory.DIVERGENT.value
    if score < thresholds.get("near_match",
                                  DEFAULT_THRESHOLDS["near_match"]) \
            and not has_material:
        return DivergenceCategory.NEAR_MATCH.value
    return DivergenceCategory.PARTIAL_MATCH.value


# --------------------------------------------------------------------------
# Privacy helpers
# --------------------------------------------------------------------------


def _value_hash(value: Any) -> str:
    """Hash a value to sha256:16hex for privacy-safe storage."""
    if value is None:
        return "sha256:" + hashlib.sha256(b"\x00null").hexdigest()[:16]
    if isinstance(value, (dict, list)):
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8")
    else:
        canonical = str(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]


def _redact_justification(field_path: str, diff_class: str,
                            severity: str) -> str:
    """Build a one-line justification with NO raw payload content."""
    return f"path={field_path} diff={diff_class} sev={severity}"


def _incomparable_detail(exc: Exception) -> DivergenceDetail:
    """Build sanitized forensic evidence for an incomparable payload."""
    diff = DiffClass.INCOMPARABLE_INPUT.value
    severity = Severity.CRITICAL.value
    arg_types = ",".join(type(arg).__name__ for arg in exc.args[:3])
    reason = f"reason={exc.__class__.__name__}"
    if arg_types:
        reason = f"{reason} arg_types={arg_types}"
    return DivergenceDetail(
        field_path="/",
        candidate_value_hash=_value_hash(None),
        baseline_value_hash=_value_hash(None),
        diff_class=diff,
        severity=severity,
        justification=(
            f"{_redact_justification('/', diff, severity)} {reason}"
        ),
    )


# --------------------------------------------------------------------------
# Format comparators
# --------------------------------------------------------------------------


def _flatten_json(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten JSON into JSON-pointer-style path -> value map."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj:
            out[prefix or "/"] = {}
        for k, v in obj.items():
            child = f"{prefix}/{k}" if prefix else f"/{k}"
            out.update(_flatten_json(v, child))
    elif isinstance(obj, list):
        if not obj:
            out[prefix or "/"] = []
        for i, v in enumerate(obj):
            child = f"{prefix}[{i}]"
            out.update(_flatten_json(v, child))
    else:
        out[prefix or "/"] = obj
    return out


def compare_json(candidate: Any, baseline: Any, *,
                  template_family: str) -> ComparisonResult:
    """Per-field JSON-pointer diff with real counts.

    n_compared counts every leaf path in the union of candidate and
    baseline. n_matching counts paths present in both AND with equal
    values.
    """
    flat_c = _flatten_json(candidate)
    flat_b = _flatten_json(baseline)
    paths = set(flat_c) | set(flat_b)
    details: list[DivergenceDetail] = []
    n_matching = 0
    for path in sorted(paths):
        in_c = path in flat_c
        in_b = path in flat_b
        if in_c and not in_b:
            diff = DiffClass.ADDED.value
            sev = _severity_for(template_family, diff)
            details.append(DivergenceDetail(
                field_path=path,
                candidate_value_hash=_value_hash(flat_c[path]),
                baseline_value_hash=_value_hash(None),
                diff_class=diff,
                severity=sev,
                justification=_redact_justification(path, diff, sev),
            ))
        elif in_b and not in_c:
            diff = DiffClass.REMOVED.value
            sev = _severity_for(template_family, diff)
            details.append(DivergenceDetail(
                field_path=path,
                candidate_value_hash=_value_hash(None),
                baseline_value_hash=_value_hash(flat_b[path]),
                diff_class=diff,
                severity=sev,
                justification=_redact_justification(path, diff, sev),
            ))
        elif flat_c[path] != flat_b[path]:
            cv, bv = flat_c[path], flat_b[path]
            diff = (DiffClass.TYPE_CHANGED.value
                     if type(cv) is not type(bv)
                     else DiffClass.CHANGED.value)
            sev = _severity_for(template_family, diff)
            details.append(DivergenceDetail(
                field_path=path,
                candidate_value_hash=_value_hash(cv),
                baseline_value_hash=_value_hash(bv),
                diff_class=diff,
                severity=sev,
                justification=_redact_justification(path, diff, sev),
            ))
        else:
            n_matching += 1
    return ComparisonResult(details=details, n_compared=len(paths),
                              n_matching=n_matching)


def compare_csv(candidate_text: str, baseline_text: str, *,
                  template_family: str) -> ComparisonResult:
    """Per-row + per-cell CSV diff.

    n_compared counts each row position in the union, plus each cell
    position within rows present in both. n_matching counts cells
    equal in both AND rows that are entirely matching.
    """
    c_rows = list(csv.reader(io.StringIO(candidate_text)))
    b_rows = list(csv.reader(io.StringIO(baseline_text)))
    details: list[DivergenceDetail] = []
    n_compared = 0
    n_matching = 0
    max_rows = max(len(c_rows), len(b_rows))
    for r in range(max_rows):
        n_compared += 1

        c_row = c_rows[r] if r < len(c_rows) else None
        b_row = b_rows[r] if r < len(b_rows) else None
        if c_row is None:
            details.append(DivergenceDetail(
                field_path=f"row[{r}]",
                candidate_value_hash=_value_hash(None),
                baseline_value_hash=_value_hash(b_row),
                diff_class=DiffClass.REMOVED.value,
                severity=_severity_for(template_family,
                                         DiffClass.REMOVED.value),
                justification=_redact_justification(
                    f"row[{r}]", DiffClass.REMOVED.value,
                    _severity_for(template_family, DiffClass.REMOVED.value)
                ),
            ))
            continue
        if b_row is None:
            details.append(DivergenceDetail(
                field_path=f"row[{r}]",
                candidate_value_hash=_value_hash(c_row),
                baseline_value_hash=_value_hash(None),
                diff_class=DiffClass.ADDED.value,
                severity=_severity_for(template_family,
                                         DiffClass.ADDED.value),
                justification=_redact_justification(
                    f"row[{r}]", DiffClass.ADDED.value,
                    _severity_for(template_family, DiffClass.ADDED.value)
                ),
            ))
            continue
        max_cols = max(len(c_row), len(b_row))
        row_all_match = True
        for col in range(max_cols):
            n_compared += 1
            c_cell = c_row[col] if col < len(c_row) else None
            b_cell = b_row[col] if col < len(b_row) else None
            if c_cell != b_cell:
                row_all_match = False
                diff = DiffClass.CHANGED.value
                sev = _severity_for(template_family, diff)
                details.append(DivergenceDetail(
                    field_path=f"row[{r}].col[{col}]",
                    candidate_value_hash=_value_hash(c_cell),
                    baseline_value_hash=_value_hash(b_cell),
                    diff_class=diff,
                    severity=sev,
                    justification=_redact_justification(
                        f"row[{r}].col[{col}]", diff, sev
                    ),
                ))
            else:
                n_matching += 1
        if row_all_match:
            # row-level match also counts (the row position unit)
            n_matching += 1
    return ComparisonResult(details=details, n_compared=n_compared,
                              n_matching=n_matching)


_SQL_STMT_RE = re.compile(r"\s*;\s*", re.MULTILINE)


def compare_sql_diff(candidate_text: str, baseline_text: str, *,
                       template_family: str) -> ComparisonResult:
    """Multi-statement SQL diff treated as set comparison.

    v1 normalizes whitespace + lowercase + strips trailing semicolons.
    Template families that require ordered execution should pre-process
    via ProfileConfig.divergence_overrides.canonicalization (not
    auto-applied here).

    n_compared counts every distinct statement in the union; n_matching
    counts statements present in BOTH sets (intersection).
    """
    def _normalize(text: str) -> list[str]:
        stmts = _SQL_STMT_RE.split(text.strip())
        return [
            " ".join(s.lower().split()) for s in stmts if s.strip()
        ]

    c_set = set(_normalize(candidate_text))
    b_set = set(_normalize(baseline_text))
    c_only = c_set - b_set
    b_only = b_set - c_set
    intersection = c_set & b_set
    details: list[DivergenceDetail] = []
    for i, stmt in enumerate(sorted(c_only)):
        sev = _severity_for(template_family, DiffClass.ADDED.value)
        details.append(DivergenceDetail(
            field_path=f"sql_stmt_added[{i}]",
            candidate_value_hash=_value_hash(stmt),
            baseline_value_hash=_value_hash(None),
            diff_class=DiffClass.ADDED.value,
            severity=sev,
            justification=_redact_justification(
                f"sql_stmt_added[{i}]", DiffClass.ADDED.value, sev
            ),
        ))
    for i, stmt in enumerate(sorted(b_only)):
        sev = _severity_for(template_family, DiffClass.REMOVED.value)
        details.append(DivergenceDetail(
            field_path=f"sql_stmt_removed[{i}]",
            candidate_value_hash=_value_hash(None),
            baseline_value_hash=_value_hash(stmt),
            diff_class=DiffClass.REMOVED.value,
            severity=sev,
            justification=_redact_justification(
                f"sql_stmt_removed[{i}]", DiffClass.REMOVED.value, sev
            ),
        ))
    return ComparisonResult(
        details=details,
        n_compared=len(c_set | b_set),
        n_matching=len(intersection),
    )


def compare_filesystem(candidate_tree: dict[str, str],
                        baseline_tree: dict[str, str], *,
                        template_family: str) -> ComparisonResult:
    """Compare filesystem snapshots {path: content_sha256}.

    Caller computes hashes; analyzer only consumes the mapping.
    n_compared counts every path in the union; n_matching counts
    paths present in both with equal content hashes.
    """
    details: list[DivergenceDetail] = []
    paths = set(candidate_tree) | set(baseline_tree)
    n_matching = 0
    for p in sorted(paths):
        in_c = p in candidate_tree
        in_b = p in baseline_tree
        if in_c and not in_b:
            sev = _severity_for(template_family, DiffClass.ADDED.value)
            details.append(DivergenceDetail(
                field_path=p,
                candidate_value_hash=_value_hash(candidate_tree[p]),
                baseline_value_hash=_value_hash(None),
                diff_class=DiffClass.ADDED.value,
                severity=sev,
                justification=_redact_justification(p, DiffClass.ADDED.value,
                                                       sev),
            ))
        elif in_b and not in_c:
            sev = _severity_for(template_family, DiffClass.REMOVED.value)
            details.append(DivergenceDetail(
                field_path=p,
                candidate_value_hash=_value_hash(None),
                baseline_value_hash=_value_hash(baseline_tree[p]),
                diff_class=DiffClass.REMOVED.value,
                severity=sev,
                justification=_redact_justification(p,
                                                       DiffClass.REMOVED.value,
                                                       sev),
            ))
        elif candidate_tree[p] != baseline_tree[p]:
            sev = _severity_for(template_family, DiffClass.CHANGED.value)
            details.append(DivergenceDetail(
                field_path=p,
                candidate_value_hash=_value_hash(candidate_tree[p]),
                baseline_value_hash=_value_hash(baseline_tree[p]),
                diff_class=DiffClass.CHANGED.value,
                severity=sev,
                justification=_redact_justification(
                    p, DiffClass.CHANGED.value, sev
                ),
            ))
        else:
            n_matching += 1
    return ComparisonResult(details=details, n_compared=len(paths),
                              n_matching=n_matching)


def _levenshtein(a: str, b: str) -> int:
    """Classic O(m*n) Levenshtein for short text diffing.

    v1 keeps a simple implementation; large texts route through the
    shared embedding service (spec edit E9) which is not wired in v1.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def compare_text(candidate_text: str, baseline_text: str, *,
                  template_family: str,
                  embedding_similarity: Optional[
                      Callable[[str, str], float]
                  ] = None) -> ComparisonResult:
    """Free-form text comparison.

    v1 uses Levenshtein-based edit ratio. Embedding similarity is
    optionally provided by the caller (spec edit E9: share the
    embedding service, do not load a second model).

    n_compared is always 1 (a single text-blob comparison). n_matching
    is 1 if identical, else 0.
    """
    if candidate_text == baseline_text:
        return ComparisonResult(details=[], n_compared=1, n_matching=1)
    edit = _levenshtein(candidate_text, baseline_text)
    max_len = max(len(candidate_text), len(baseline_text), 1)
    edit_ratio = edit / max_len
    sim = (embedding_similarity(candidate_text, baseline_text)
            if embedding_similarity is not None else 1.0 - edit_ratio)
    if sim > 0.9:
        sev = Severity.NOISE.value
    elif sim > 0.7:
        sev = Severity.MINOR.value
    else:
        sev = _severity_for(template_family, DiffClass.CHANGED.value)
    detail = DivergenceDetail(
        field_path="/",
        candidate_value_hash=_value_hash(candidate_text),
        baseline_value_hash=_value_hash(baseline_text),
        diff_class=DiffClass.CHANGED.value,
        severity=sev,
        justification=f"edit_ratio={edit_ratio:.3f} sim={sim:.3f} sev={sev}",
    )
    return ComparisonResult(details=[detail], n_compared=1, n_matching=0)


# --------------------------------------------------------------------------
# DivergenceAnalyzer itself
# --------------------------------------------------------------------------


@dataclass
class DivergenceAnalyzer:
    """Compare candidate vs baseline outputs and produce a score."""

    emit_magma_event: Callable[[dict], str]
    persist_summary: Callable[[list[DivergenceDetail]], str]
    """Persist the detail list (possibly chunked beyond 100 entries per
    spec edit E11) and return an artifact URI."""

    embedding_similarity: Optional[
        Callable[[str, str], float]
    ] = None
    thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_THRESHOLDS)
    )
    detail_chunk_threshold: int = 100              # spec edit E11

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def compare(self, *, candidate_output_uri: str,
                  baseline_output_uri: str,
                  candidate_payload: Any,
                  baseline_payload: Any,
                  expected_output_format: str,
                  template_family: str) -> DivergenceArtifact:
        """Compare two outputs and return a DivergenceArtifact.

        candidate_payload + baseline_payload types depend on format:
        * json: any Python object decoded from JSON
        * csv: str (raw CSV text)
        * sql_diff: str (semicolon-delimited SQL statements)
        * filesystem: dict[path, content_sha256]
        * text: str

        If format mismatch makes comparison impossible, returns an
        INCOMPARABLE artifact with no details (spec edit E10).
        """
        incomparable = False
        details: list[DivergenceDetail] = []
        n_compared = 0
        n_matching = 0

        try:
            if expected_output_format == "json":
                result = compare_json(candidate_payload, baseline_payload,
                                         template_family=template_family)
            elif expected_output_format == "csv":
                result = compare_csv(candidate_payload, baseline_payload,
                                        template_family=template_family)
            elif expected_output_format == "sql_diff":
                result = compare_sql_diff(candidate_payload, baseline_payload,
                                              template_family=template_family)
            elif expected_output_format == "filesystem":
                result = compare_filesystem(candidate_payload,
                                                baseline_payload,
                                                template_family=template_family)
            elif expected_output_format == "text":
                result = compare_text(
                    candidate_payload, baseline_payload,
                    template_family=template_family,
                    embedding_similarity=self.embedding_similarity,
                )
            else:
                incomparable = True
                result = None
            if result is not None:
                details = result.details
                n_compared = result.n_compared
                n_matching = result.n_matching
        except (TypeError, ValueError, AttributeError) as exc:
            # Shape mismatch -> incomparable (spec edit E10)
            incomparable = True
            details = [_incomparable_detail(exc)]

        score = self._aggregate_score(
            details, n_compared=n_compared, n_matching=n_matching,
            incomparable=incomparable,
        )
        has_material = any(d.severity in (Severity.MATERIAL.value,
                                              Severity.CRITICAL.value)
                              for d in details)
        has_critical = any(d.severity == Severity.CRITICAL.value
                              for d in details)
        category = _category_for(
            score.score, has_material=has_material,
            has_critical=has_critical, incomparable=incomparable,
            thresholds=self.thresholds,
        )
        score.category = category

        # Persist summary; if > threshold, persist_summary handles
        # chunking per spec edit E11.
        summary_ref = (
            self.persist_summary(details) if details else None
        )

        # Emit MAGMA event. Header event + chunk pointer.
        artifact_id = str(uuid.uuid4())
        evt = {
            "event_type": "divergence.scored",
            "artifact_id": artifact_id,
            "candidate_output_uri": candidate_output_uri,
            "baseline_output_uri": baseline_output_uri,
            "template_family": template_family,
            "score": score.score,
            "category": score.category,
            "n_details": len(details),
            "n_critical": sum(1 for d in details
                                  if d.severity == Severity.CRITICAL.value),
            "n_material": sum(1 for d in details
                                  if d.severity == Severity.MATERIAL.value),
            "delta_summary_ref": summary_ref,
            "ts_utc": _utc_iso(),
        }
        audit_id = self.emit_magma_event(evt)

        operator_review_required = (
            has_material or category == DivergenceCategory.INCOMPARABLE.value
        )

        return DivergenceArtifact(
            artifact_id=artifact_id,
            candidate_output_uri=candidate_output_uri,
            baseline_output_uri=baseline_output_uri,
            score=score,
            details=details,
            template_family=template_family,
            operator_review_required=operator_review_required,
            audit_event_id=audit_id,
            delta_summary_ref=summary_ref,
        )

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _aggregate_score(self, details: list[DivergenceDetail], *,
                           n_compared: int, n_matching: int,
                           incomparable: bool) -> DivergenceScore:
        if incomparable:
            return DivergenceScore(
                score=1.0,
                category=DivergenceCategory.INCOMPARABLE.value,
                n_fields_compared=0,
                n_fields_matching=0,
                n_fields_diverging=0,
            )
        if not details:
            return DivergenceScore(
                score=0.0,
                category=DivergenceCategory.IDENTICAL.value,
                n_fields_compared=n_compared,
                n_fields_matching=n_matching,
                n_fields_diverging=0,
            )
        # Severity-weighted score normalised over the TOTAL comparable
        # surface (not just the diverging subset). This lets a single
        # diff in a 100-field payload yield a much lower score than the
        # same diff in a 1-field payload, which is the correct INST-G09
        # signal.
        weights = {
            Severity.NOISE.value: 0.01,
            Severity.MINOR.value: 0.05,
            Severity.MATERIAL.value: 0.20,
            Severity.CRITICAL.value: 0.50,
        }
        total = sum(weights.get(d.severity, 0.20) for d in details)
        denom = max(n_compared, len(details), 1)
        score = min(1.0, total / denom)
        n_div = sum(1 for d in details
                     if d.severity != Severity.NOISE.value)
        return DivergenceScore(
            score=score,
            category=DivergenceCategory.IDENTICAL.value,   # overwritten
            n_fields_compared=n_compared,
            n_fields_matching=n_matching,
            n_fields_diverging=n_div,
        )


# --------------------------------------------------------------------------
# INST-G09 aggregator
# --------------------------------------------------------------------------


@dataclass
class InstG09Aggregate:
    """Aggregate over a sliding window of DivergenceArtifacts."""

    window_size: int                              # number of artifacts
    non_divergent_count: int
    divergent_count: int
    has_critical: bool
    non_divergent_pct: float


def inst_g09_aggregate(artifacts: list[DivergenceArtifact]
                          ) -> InstG09Aggregate:
    """Compute the INST-G09 acceptance signal.

    Promotion to hybrid requires:
    * non_divergent_pct >= 95% (category in {identical, near_match})
    * has_critical == False
    """
    if not artifacts:
        return InstG09Aggregate(window_size=0, non_divergent_count=0,
                                 divergent_count=0, has_critical=False,
                                 non_divergent_pct=0.0)
    non_divergent = sum(1 for a in artifacts
                          if a.score.category in (
                              DivergenceCategory.IDENTICAL.value,
                              DivergenceCategory.NEAR_MATCH.value,
                          ))
    divergent = len(artifacts) - non_divergent
    has_critical = any(
        any(d.severity == Severity.CRITICAL.value for d in a.details)
        for a in artifacts
    )
    return InstG09Aggregate(
        window_size=len(artifacts),
        non_divergent_count=non_divergent,
        divergent_count=divergent,
        has_critical=has_critical,
        non_divergent_pct=non_divergent / len(artifacts),
    )


def inst_g09_passes(agg: InstG09Aggregate, *,
                      pct_threshold: float = 0.95) -> bool:
    return (agg.non_divergent_pct >= pct_threshold and not agg.has_critical)


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "DivergenceCategory",
    "DiffClass",
    "Severity",
    "DivergenceScore",
    "DivergenceDetail",
    "DivergenceArtifact",
    "DivergenceAnalyzer",
    "SEVERITY_RULES",
    "DEFAULT_THRESHOLDS",
    "compare_json",
    "compare_csv",
    "compare_sql_diff",
    "compare_filesystem",
    "compare_text",
    "InstG09Aggregate",
    "inst_g09_aggregate",
    "inst_g09_passes",
]
