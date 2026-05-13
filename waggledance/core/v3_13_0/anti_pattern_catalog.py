# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""ANTI-001..007 invariant catalog: concrete implementations.

Each ANTI-NNN rule is a callable that takes context and returns
either None (rule does not fire) or an InvariantViolation
(rule fires, includes audit detail).

The catalog is consumed by WriteRCOGate (per write_rco_gate_v1_spec
WRT-002 credential scan + WRT-003 anti-pattern check), SchemaUnifier
(date_sort check), AuditProjector (memory.append_only), and the
production-safety guards in ScheduledIncrementalSync.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/anti_pattern_invariant_catalog.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# Module-scope compiled regex patterns
# --------------------------------------------------------------------------
# Repo contract test_regex_compile_outside_hot_path enforces that
# re.compile() lives at module scope so patterns are not rebuilt per
# call. All catalog patterns are defined here.

# ANTI-005 silent-drop static lint pattern: try / parse / except /
# continue|pass with no logging / quarantine / UNPARSED sentinel.
_RE_ANTI_005_SILENT_DROP = re.compile(
    r"try:\s*\n"
    r"(?:[ \t]+[^\n]+\n)+"
    r"[ \t]*except[^:]*:\s*\n"
    r"[ \t]+(continue|pass)\s*\n",
    re.MULTILINE,
)

_RE_ANTI_005_PARSE_CONTEXT = re.compile(
    r"\b(parse|decode|json\.loads|fromisoformat)\b"
)


# --------------------------------------------------------------------------
# Violation data type
# --------------------------------------------------------------------------


@dataclass
class InvariantViolation:
    """A single ANTI-NNN rule firing. Returned by the rule check."""

    anti_id: str                              # e.g. "ANTI-001"
    name: str                                 # short identifier
    reason: str                               # human-readable
    blocked: bool = True                      # True = refused;
                                              # False = warning only
    exception_path: str = ""                  # operator scope policy
                                              # ref if exception exists
    audit_event_type: str = ""                # MAGMA event type
    detail: dict = None                       # rule-specific context

    def to_audit_envelope(self, *, agent_id: str = "",
                            session_id: str = "",
                            extra: Optional[dict] = None) -> dict:
        """Format as a MAGMA-bound audit event."""
        env = {
            "event_type": self.audit_event_type or
                            f"safety.{self.name}",
            "anti_id": self.anti_id,
            "name": self.name,
            "reason": self.reason,
            "blocked": self.blocked,
            "exception_path": self.exception_path,
            "ts_utc": _utc_iso(),
            "agent_id": agent_id,
            "session_id": session_id,
        }
        if self.detail:
            env["detail"] = self.detail
        if extra:
            env.update(extra)
        return env


# --------------------------------------------------------------------------
# ANTI-001: production_safety.bulk_read_without_window_blocked
# --------------------------------------------------------------------------


def anti_001_bulk_read_without_window(
    *,
    connector_shared_production: bool,
    call_args: dict,
    bulk_read_authorized: bool = False,
) -> Optional[InvariantViolation]:
    """ANTI-001: bulk read from shared production upstream requires
    a bounded time window or explicit bulk_read_authorized scope.

    Args:
        connector_shared_production: True if the connector is marked
          shared production (per ProfileConfig / AuthenticatedConnector).
        call_args: kwargs the caller is about to pass to the upstream.
          Must include at least one of: 'begin_date', 'since', 'limit',
          'window_seconds', 'limit_rows' to be a windowed call.
        bulk_read_authorized: True if operator scope policy has
          explicitly authorized this bulk read.
    """
    if not connector_shared_production:
        return None
    if bulk_read_authorized:
        return None
    windowed_keys = {"begin_date", "since", "limit", "window_seconds",
                      "limit_rows", "from_ts", "after"}
    if any(k in call_args for k in windowed_keys):
        return None
    return InvariantViolation(
        anti_id="ANTI-001",
        name="bulk_read_without_window",
        reason=(
            "shared production upstream requires bounded time window "
            "(begin_date / since / limit / window_seconds / limit_rows / "
            "from_ts / after) or explicit operator bulk_read_authorized "
            "scope"
        ),
        blocked=True,
        exception_path="operator scope policy: bulk_read_authorized = True",
        audit_event_type="safety.bulk_read_attempted",
        detail={"call_args_keys": sorted(call_args.keys())},
    )


# --------------------------------------------------------------------------
# ANTI-002: date_sort.text_columns_blocked
# --------------------------------------------------------------------------


def anti_002_text_date_sort(
    *,
    sql: str,
    column_types: dict[str, str],
) -> Optional[InvariantViolation]:
    """ANTI-002: ORDER BY / MAX / MIN on a TEXT date-like column
    is blocked. Caller must use the *_iso mirror column instead.

    Args:
        sql: the SQL statement about to execute.
        column_types: mapping column_name -> column_type (e.g.
          'date': 'TEXT', 'date_iso': 'TEXT' [ISO-8601 enforced],
          'created_at': 'TEXT').
    """
    sql_lower = sql.lower()
    # Normalise column metadata: lowercase the column name for matching
    # so callers can pass 'Date' / 'CreatedAt' / quoted variants and the
    # check still fires. Per Codex RCO round-2 fix.
    text_date_cols = [
        col.lower() for col, t in column_types.items()
        if t.upper() == "TEXT"
        and col.lower() in {"date", "created_at", "updated_at",
                              "ts", "timestamp", "logged_at"}
        and not col.lower().endswith("_iso")
    ]
    if not text_date_cols:
        return None
    for col in text_date_cols:
        col_re = re.escape(col)
        # Optional table-qualifier prefix (e.g. t.date, alias."date",
        # `t`.`date`). The qualifier-and-dot is non-capturing and
        # optional; the column itself can be bare, double-quoted,
        # backtick-quoted, or bracket-quoted (SQL Server style).
        qualifier = r"(?:[a-z0-9_]+\s*\.\s*)?"
        bare_or_quoted = rf"(?:{col_re}|\"{col_re}\"|`{col_re}`|\[{col_re}\])"
        col_pat = qualifier + bare_or_quoted
        patterns = [
            rf"\border\s+by\s+{col_pat}\b",
            rf"\bmax\s*\(\s*{col_pat}\s*\)",
            rf"\bmin\s*\(\s*{col_pat}\s*\)",
        ]
        for pat in patterns:
            if re.search(pat, sql_lower):
                return InvariantViolation(
                    anti_id="ANTI-002",
                    name="text_date_sort",
                    reason=(
                        f"sorting / max / min on TEXT date column "
                        f"{col!r} is blocked; use {col}_iso mirror "
                        f"column instead"
                    ),
                    blocked=True,
                    exception_path=(
                        "none; SchemaUnifier auto-creates *_iso mirror"
                    ),
                    audit_event_type="schema.text_date_sort_blocked",
                    detail={"column": col,
                            "sql_snippet": sql[:200]},
                )
    return None


# --------------------------------------------------------------------------
# ANTI-003: sqlite_wal.parallel_writers_blocked
# --------------------------------------------------------------------------


def anti_003_parallel_writers(
    *,
    db_path: str,
    active_writers: list[str],
    new_writer_id: str,
) -> Optional[InvariantViolation]:
    """ANTI-003: SQLite WAL mode allows exactly one writer per DB
    handle. Reject a new writer if another writer is already active.

    Args:
        db_path: the SQLite DB being written.
        active_writers: list of writer IDs currently holding a write
          handle on this DB.
        new_writer_id: the writer requesting access.
    """
    if not active_writers:
        return None
    if new_writer_id in active_writers:
        # Same writer re-entering; this is fine (single instance).
        return None
    return InvariantViolation(
        anti_id="ANTI-003",
        name="parallel_writers",
        reason=(
            f"DB {db_path!r} is in WAL mode; writer {new_writer_id!r} "
            f"refused because {len(active_writers)} writer(s) already "
            f"active: {active_writers[:3]}"
        ),
        blocked=True,
        exception_path="route writes through a single-writer queue",
        audit_event_type="sqlite.parallel_writer_attempted",
        detail={"db_path": db_path,
                 "active_writers": active_writers[:5],
                 "new_writer_id": new_writer_id},
    )


# --------------------------------------------------------------------------
# ANTI-004: auth.tokens_in_repo_blocked
# --------------------------------------------------------------------------


# Known credential patterns. Order matters: more specific patterns first.
_CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_api_key", r"\bsk-[A-Za-z0-9]{20,}\b"),
    ("anthropic_api_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("github_personal_token", r"\bghp_[A-Za-z0-9]{30,}\b"),
    ("github_oauth_token", r"\bgho_[A-Za-z0-9]{30,}\b"),
    ("github_app_token", r"\b(ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    ("slack_bot_token", r"\bxox[bpsa]-[0-9-]+-[A-Za-z0-9]+\b"),
    ("google_oauth_refresh", r"\bya29\.[A-Za-z0-9._-]+\b"),
    ("aws_access_key", r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("aws_secret_key", r"(?<![A-Za-z0-9])[A-Za-z0-9/+]{40}(?![A-Za-z0-9])"),
    ("private_key_pem", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("jwt_token", r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ("basic_auth_inline", r"\bhttps?://[^/\s:]+:[^/\s@]+@"),
    ("password_assignment",
     r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
    ("api_key_assignment",
     r"(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*['\"][A-Za-z0-9_/+-]{16,}['\"]"),
)

# Allowlist: patterns that LOOK like credentials but are not.
# Add entries here when a known-false-positive is identified.
_CREDENTIAL_ALLOWLIST: tuple[str, ...] = (
    # vault://impl/tenant/scope/name -- a CredentialRef URI, not material
    r"vault://[a-z0-9_-]+/[a-z0-9_-]+/[a-z0-9_.-]+/[a-z0-9_.-]+",
    # Pure placeholder env-var references
    r"\$\{[A-Z_][A-Z0-9_]*\}",
    r"\$[A-Z_][A-Z0-9_]*\b",
)


@dataclass
class CredentialPatternHit:
    """A single match within the scanned content."""

    pattern_name: str
    matched_text_prefix: str                  # first 12 chars of match;
                                              # NEVER full match
    offset: int                               # byte offset within input


def scan_for_credential_patterns(text: str) -> list[CredentialPatternHit]:
    """Scan text for known credential patterns. Used by WriteRCOGate
    WRT-002 gate-level scan (Codex RCO edit #4) and ANTI-004 enforcement.

    Allowlist applied first so vault:// URIs and ${ENV_VAR} refs are
    not flagged.

    Returns a list of CredentialPatternHit; empty list means clean.
    The hit's matched_text_prefix never contains the full matched
    string -- only the first 12 chars -- so the result itself does
    not leak the credential.
    """
    if not text:
        return []
    # Mask allowlisted regions before scanning. The mask character ' '
    # is outside the credential pattern character classes, so masked
    # regions cannot match any pattern.
    masked = text
    for allow_pat in _CREDENTIAL_ALLOWLIST:
        masked = re.sub(allow_pat, lambda m: " " * len(m.group(0)), masked)

    hits: list[CredentialPatternHit] = []
    for name, pattern in _CREDENTIAL_PATTERNS:
        for m in re.finditer(pattern, masked):
            full_match = m.group(0)
            hits.append(CredentialPatternHit(
                pattern_name=name,
                matched_text_prefix=full_match[:12] + "...",
                offset=m.start(),
            ))
            # One hit per pattern is enough to fail the scan; continue
            # scanning other patterns so we surface all categories
            break
    return hits


def anti_004_credential_in_content(
    *,
    content: str,
    tracked_for_commit: bool,
) -> Optional[InvariantViolation]:
    """ANTI-004: credential material in tracked content is blocked.

    Args:
        content: the text being checked (file body, payload, etc.).
        tracked_for_commit: True if this content will be tracked by
          git (i.e., not gitignored, not stash, not a temp scratch).
    """
    if not tracked_for_commit:
        # Content not destined for the repo; ANTI-004 does not fire,
        # though callers may still want the scan results for other
        # gates.
        return None
    hits = scan_for_credential_patterns(content)
    if not hits:
        return None
    return InvariantViolation(
        anti_id="ANTI-004",
        name="credential_in_repo",
        reason=(
            f"credential pattern(s) detected in tracked content: "
            f"{[h.pattern_name for h in hits[:5]]}"
        ),
        blocked=True,
        exception_path=(
            "none; credentials live in CredentialVault, referenced "
            "by vault://impl/tenant/scope/name URIs"
        ),
        audit_event_type="auth.credential_in_repo_blocked",
        detail={
            "hit_count": len(hits),
            "pattern_names": [h.pattern_name for h in hits[:10]],
            "first_offsets": [h.offset for h in hits[:10]],
        },
    )


# --------------------------------------------------------------------------
# ANTI-005: parser.no_silent_fail
# --------------------------------------------------------------------------


def make_no_silent_fail_parser(
    inner_parser: Callable[[str], Any],
    *,
    quarantine_emit: Callable[[dict], None],
    parser_name: str = "unnamed",
) -> Callable[[str], Any]:
    """Wraps an inner_parser so that parse failures emit a
    parser.unparsed_recorded MAGMA event (Codex RCO edit #9: MAGMA
    event stream is the canonical quarantine target) and return the
    explicit 'UNPARSED' sentinel rather than silently dropping the
    row.

    Args:
        inner_parser: the parser function (str -> Any).
        quarantine_emit: callable that emits the MAGMA event.
        parser_name: identifier for the parser in audit envelope.

    Returns:
        A wrapped parser that never raises and never silently drops.
    """
    def wrapped(raw: str):
        try:
            return inner_parser(raw)
        except Exception as exc:
            # Sanitize the error string: many built-in parsers
            # (int, float, datetime.fromisoformat) embed the raw
            # input in their exception message, which would leak it
            # into the audit envelope. Record only the exception
            # class name; the raw_hash + raw_length give enough
            # context for operator triage without leaking content.
            quarantine_emit({
                "event_type": "parser.unparsed_recorded",
                "parser_name": parser_name,
                "parse_error_class": type(exc).__name__,
                "raw_hash": _sha256_hex(raw)[:16] if raw else "",
                "raw_length": len(raw) if raw else 0,
                "ts_utc": _utc_iso(),
            })
            return "UNPARSED"
    return wrapped


def anti_005_silent_drop_in_code(
    *,
    source_code: str,
) -> Optional[InvariantViolation]:
    """ANTI-005 static check (lint-style): flag try/except patterns
    that silently drop on parse failure.

    Args:
        source_code: Python source as a string to lint.

    Looks for:
        try:
            parse(...)
        except ...:
            continue            # or pass

    Returns InvariantViolation if a silent-drop pattern is detected.
    """
    if not source_code:
        return None
    # Pattern compiled at module scope; see _RE_ANTI_005_SILENT_DROP.
    matches = list(_RE_ANTI_005_SILENT_DROP.finditer(source_code))
    if not matches:
        return None
    # Examine context for parse / decode / json calls
    relevant = []
    for m in matches:
        snippet = m.group(0)
        if _RE_ANTI_005_PARSE_CONTEXT.search(snippet):
            line_no = source_code[:m.start()].count("\n") + 1
            relevant.append((line_no, snippet[:200]))
    if not relevant:
        return None
    return InvariantViolation(
        anti_id="ANTI-005",
        name="parser_silent_drop",
        reason=(
            f"{len(relevant)} try/except parse pattern(s) without "
            f"explicit UNPARSED sentinel or quarantine emit detected"
        ),
        blocked=False,        # static lint = warning, not block
        exception_path="wrap parser with make_no_silent_fail_parser",
        audit_event_type="parser.silent_skip_lint",
        detail={"locations": relevant[:5]},
    )


# --------------------------------------------------------------------------
# ANTI-006: api.rate_limit_per_upstream
# --------------------------------------------------------------------------


def anti_006_rate_limit_exceeded(
    *,
    upstream_id: str,
    declared_max_workers: int,
    declared_request_delay_s: float,
    attempted_concurrent_workers: int,
    attempted_request_delay_s: float,
) -> Optional[InvariantViolation]:
    """ANTI-006: rate limits per upstream are enforced.

    Args:
        upstream_id: identifier for the upstream service.
        declared_max_workers: ToolDescriptor/Connector declared cap.
        declared_request_delay_s: declared minimum inter-request delay.
        attempted_concurrent_workers: workers the caller is about to spin up.
        attempted_request_delay_s: actual delay caller plans to use.
    """
    if attempted_concurrent_workers > declared_max_workers:
        return InvariantViolation(
            anti_id="ANTI-006",
            name="rate_limit_workers_exceeded",
            reason=(
                f"upstream {upstream_id} declared max_workers="
                f"{declared_max_workers}; caller attempted "
                f"{attempted_concurrent_workers}"
            ),
            blocked=True,
            exception_path=(
                "operator scope policy may raise the limit per "
                "upstream-side authorisation"
            ),
            audit_event_type="api.rate_limit_violated",
            detail={"upstream": upstream_id,
                     "declared_max_workers": declared_max_workers,
                     "attempted_workers": attempted_concurrent_workers},
        )
    if attempted_request_delay_s < declared_request_delay_s:
        return InvariantViolation(
            anti_id="ANTI-006",
            name="rate_limit_delay_too_short",
            reason=(
                f"upstream {upstream_id} declared min request_delay_s="
                f"{declared_request_delay_s}; caller attempted "
                f"{attempted_request_delay_s}"
            ),
            blocked=True,
            exception_path="operator scope policy may lower the floor",
            audit_event_type="api.rate_limit_violated",
            detail={"upstream": upstream_id,
                     "declared_delay_s": declared_request_delay_s,
                     "attempted_delay_s": attempted_request_delay_s},
        )
    return None


# --------------------------------------------------------------------------
# ANTI-007: memory.append_only
# --------------------------------------------------------------------------


def anti_007_original_layer_modification(
    *,
    target_layer: str,
    action: str,
) -> Optional[InvariantViolation]:
    """ANTI-007: the 'original' memory layer is immutable.

    Args:
        target_layer: which memory layer the action targets.
          One of: original / correction / working / session / enrichment.
        action: insert / update / delete / append.
    """
    if target_layer != "original":
        return None
    if action in ("update", "delete"):
        return InvariantViolation(
            anti_id="ANTI-007",
            name="original_layer_modification",
            reason=(
                f"action {action!r} blocked on memory layer 'original'; "
                f"corrections must use the correction layer with provenance "
                f"pointer"
            ),
            blocked=True,
            exception_path=(
                "use MemoryWriteProxy correction layer or invalidate_range"
            ),
            audit_event_type="memory.original_layer_modification_attempted",
            detail={"target_layer": target_layer, "action": action},
        )
    return None


# --------------------------------------------------------------------------
# Convenience: full catalog scan over a write intent
# --------------------------------------------------------------------------


_ALL_INVARIANTS_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("ANTI-001",
     "production_safety.bulk_read_without_window_blocked"),
    ("ANTI-002", "date_sort.text_columns_blocked"),
    ("ANTI-003", "sqlite_wal.parallel_writers_blocked"),
    ("ANTI-004", "auth.tokens_in_repo_blocked"),
    ("ANTI-005", "parser.no_silent_fail"),
    ("ANTI-006", "api.rate_limit_per_upstream"),
    ("ANTI-007", "memory.append_only"),
)


def list_invariants() -> tuple[tuple[str, str], ...]:
    """Return ((id, name), ...) for all invariants in the catalog."""
    return _ALL_INVARIANTS_DESCRIPTIONS


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _sha256_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


__all__ = [
    "InvariantViolation",
    "CredentialPatternHit",
    "scan_for_credential_patterns",
    "anti_001_bulk_read_without_window",
    "anti_002_text_date_sort",
    "anti_003_parallel_writers",
    "anti_004_credential_in_content",
    "make_no_silent_fail_parser",
    "anti_005_silent_drop_in_code",
    "anti_006_rate_limit_exceeded",
    "anti_007_original_layer_modification",
    "list_invariants",
]
