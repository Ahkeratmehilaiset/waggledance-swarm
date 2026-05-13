# SPDX-License-Identifier: BUSL-1.1
"""Tests for ANTI-001..007 invariant catalog."""
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.anti_pattern_catalog import (
    InvariantViolation,
    CredentialPatternHit,
    scan_for_credential_patterns,
    anti_001_bulk_read_without_window,
    anti_002_text_date_sort,
    anti_003_parallel_writers,
    anti_004_credential_in_content,
    make_no_silent_fail_parser,
    anti_005_silent_drop_in_code,
    anti_006_rate_limit_exceeded,
    anti_007_original_layer_modification,
    list_invariants,
)


# ============================================================================
# ANTI-001: bulk_read_without_window
# ============================================================================


class TestAnti001BulkRead:

    def test_no_violation_when_not_shared_production(self):
        result = anti_001_bulk_read_without_window(
            connector_shared_production=False,
            call_args={},
        )
        assert result is None

    def test_violation_when_no_window_on_shared_production(self):
        result = anti_001_bulk_read_without_window(
            connector_shared_production=True,
            call_args={"log_code": "em-repair-wp1"},
        )
        assert result is not None
        assert result.anti_id == "ANTI-001"
        assert result.blocked is True
        assert "windowed" not in result.reason.lower() or \
            "bounded" in result.reason.lower()

    def test_no_violation_with_begin_date(self):
        result = anti_001_bulk_read_without_window(
            connector_shared_production=True,
            call_args={"log_code": "em-repair-wp1",
                        "begin_date": "2026-05-01"},
        )
        assert result is None

    def test_no_violation_with_limit(self):
        result = anti_001_bulk_read_without_window(
            connector_shared_production=True,
            call_args={"limit": 100},
        )
        assert result is None

    def test_no_violation_when_explicitly_authorized(self):
        result = anti_001_bulk_read_without_window(
            connector_shared_production=True,
            call_args={},
            bulk_read_authorized=True,
        )
        assert result is None


# ============================================================================
# ANTI-002: text_date_sort
# ============================================================================


class TestAnti002TextDateSort:

    def test_violation_on_order_by_text_date(self):
        result = anti_002_text_date_sort(
            sql="SELECT * FROM logbook_entries ORDER BY date DESC LIMIT 10",
            column_types={"date": "TEXT", "id": "INTEGER"},
        )
        assert result is not None
        assert result.anti_id == "ANTI-002"
        assert "date" in result.reason

    def test_violation_on_max_text_created_at(self):
        result = anti_002_text_date_sort(
            sql="SELECT MAX(created_at) FROM logbook_entries",
            column_types={"created_at": "TEXT"},
        )
        assert result is not None

    def test_no_violation_on_iso_mirror_column(self):
        result = anti_002_text_date_sort(
            sql="SELECT MAX(date_iso) FROM logbook_entries WHERE date_iso != 'UNPARSED'",
            column_types={"date": "TEXT", "date_iso": "TEXT"},
        )
        assert result is None

    def test_no_violation_when_column_is_not_text(self):
        # If the date column is REAL/INTEGER (e.g. epoch), it's fine.
        result = anti_002_text_date_sort(
            sql="SELECT MAX(date) FROM logbook_entries",
            column_types={"date": "INTEGER"},
        )
        assert result is None

    def test_no_violation_on_unrelated_query(self):
        result = anti_002_text_date_sort(
            sql="SELECT id, name FROM tools",
            column_types={"date": "TEXT"},
        )
        assert result is None


# ============================================================================
# ANTI-003: parallel_writers
# ============================================================================


class TestAnti003ParallelWriters:

    def test_no_violation_when_no_active_writers(self):
        result = anti_003_parallel_writers(
            db_path="data/test.db",
            active_writers=[],
            new_writer_id="sync_worker_1",
        )
        assert result is None

    def test_violation_when_other_writer_active(self):
        result = anti_003_parallel_writers(
            db_path="data/test.db",
            active_writers=["sync_worker_1"],
            new_writer_id="sync_worker_2",
        )
        assert result is not None
        assert result.anti_id == "ANTI-003"
        assert "sync_worker_1" in result.reason or \
            "sync_worker_1" in str(result.detail)

    def test_no_violation_on_same_writer_reentry(self):
        result = anti_003_parallel_writers(
            db_path="data/test.db",
            active_writers=["sync_worker_1"],
            new_writer_id="sync_worker_1",
        )
        assert result is None


# ============================================================================
# ANTI-004: credential_in_content + pattern scan
# ============================================================================


class TestCredentialPatternScan:

    def test_detects_openai_key(self):
        hits = scan_for_credential_patterns(
            "the value is sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890 here"
        )
        assert len(hits) >= 1
        assert any(h.pattern_name == "openai_api_key" for h in hits)

    def test_detects_github_token(self):
        hits = scan_for_credential_patterns(
            "TOKEN=ghp_AbCdEfGhIjKlMnOpQrStUvWxYz12345678 in env"
        )
        assert any(h.pattern_name == "github_personal_token" for h in hits)

    def test_detects_private_key_pem(self):
        hits = scan_for_credential_patterns(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        assert any(h.pattern_name == "private_key_pem" for h in hits)

    def test_detects_basic_auth_inline(self):
        hits = scan_for_credential_patterns(
            "URL=https://user:p4ssw0rd@example.com/path"
        )
        assert any(h.pattern_name == "basic_auth_inline" for h in hits)

    def test_detects_jwt(self):
        hits = scan_for_credential_patterns(
            "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert any(h.pattern_name == "jwt_token" for h in hits)

    def test_allowlist_skips_vault_uri(self):
        hits = scan_for_credential_patterns(
            "ref: vault://os_keyring/profile_42/factory_anchor/pdam_session"
        )
        # Vault refs should NOT trigger credential patterns
        # (allowlist masks them before scan)
        assert all(h.pattern_name != "api_key_assignment" for h in hits)

    def test_allowlist_skips_env_var_placeholder(self):
        hits = scan_for_credential_patterns(
            'api_key="${OPENAI_API_KEY}"'
        )
        # ${ENV_VAR} should be masked by allowlist
        assert not any(h.pattern_name == "api_key_assignment" for h in hits)

    def test_empty_content_returns_no_hits(self):
        assert scan_for_credential_patterns("") == []

    def test_hit_prefix_truncates(self):
        hits = scan_for_credential_patterns(
            "sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
        )
        assert len(hits) >= 1
        # Prefix should not contain the full match (security: don't
        # leak the secret in the hit report)
        for h in hits:
            assert "..." in h.matched_text_prefix
            assert len(h.matched_text_prefix) <= 18  # 12 + "..." + a bit


class TestAnti004CredentialInRepo:

    def test_violation_when_tracked_with_credential(self):
        result = anti_004_credential_in_content(
            content="OPENAI_API_KEY=sk-AbCdEfGhIjKlMnOpQrStUvWx1234567890",
            tracked_for_commit=True,
        )
        assert result is not None
        assert result.anti_id == "ANTI-004"
        assert result.blocked is True

    def test_no_violation_when_not_tracked(self):
        result = anti_004_credential_in_content(
            content="OPENAI_API_KEY=sk-AbCdEfGhIjKlMnOpQrStUvWx1234567890",
            tracked_for_commit=False,
        )
        assert result is None

    def test_no_violation_clean_content(self):
        result = anti_004_credential_in_content(
            content="config:\n  retries: 3\n  timeout: 30\n",
            tracked_for_commit=True,
        )
        assert result is None

    def test_no_violation_vault_uri_only(self):
        result = anti_004_credential_in_content(
            content="ref: vault://os_keyring/p_42/scope/name\n",
            tracked_for_commit=True,
        )
        assert result is None


# ============================================================================
# ANTI-005: parser silent_drop + make_no_silent_fail_parser
# ============================================================================


class TestNoSilentFailParser:

    def test_wrapper_returns_value_on_success(self):
        events = []
        wrapped = make_no_silent_fail_parser(
            int,
            quarantine_emit=lambda env: events.append(env),
            parser_name="int_parser",
        )
        assert wrapped("42") == 42
        assert events == []  # No quarantine on success

    def test_wrapper_returns_unparsed_on_failure(self):
        events = []
        wrapped = make_no_silent_fail_parser(
            int,
            quarantine_emit=lambda env: events.append(env),
            parser_name="int_parser",
        )
        result = wrapped("not_a_number")
        assert result == "UNPARSED"
        assert len(events) == 1
        assert events[0]["event_type"] == "parser.unparsed_recorded"
        assert events[0]["parser_name"] == "int_parser"

    def test_wrapper_includes_raw_hash_and_length(self):
        events = []
        wrapped = make_no_silent_fail_parser(
            int,
            quarantine_emit=lambda env: events.append(env),
            parser_name="int_parser",
        )
        wrapped("bad_input_value")
        env = events[0]
        assert env["raw_length"] == len("bad_input_value")
        assert env["raw_hash"]  # truthy 16-char hex prefix

    def test_wrapper_does_not_leak_full_raw_in_event(self):
        """Audit event should NOT contain the raw input (PII surface)."""
        events = []
        wrapped = make_no_silent_fail_parser(
            int,
            quarantine_emit=lambda env: events.append(env),
            parser_name="int_parser",
        )
        wrapped("personal_data_42_secret")
        env = events[0]
        # The full raw should not appear in any envelope field
        import json
        blob = json.dumps(env, sort_keys=True, default=str)
        assert "personal_data_42_secret" not in blob


class TestAnti005SilentDropStatic:

    def test_violation_on_silent_except_continue(self):
        bad_code = '''
def process_rows(rows):
    for row in rows:
        try:
            parse(row)
        except ValueError:
            continue
'''
        result = anti_005_silent_drop_in_code(source_code=bad_code)
        assert result is not None
        assert result.anti_id == "ANTI-005"
        assert result.blocked is False  # static lint = warning

    def test_no_violation_on_logged_except(self):
        good_code = '''
def process_rows(rows):
    for row in rows:
        try:
            parse(row)
        except ValueError as exc:
            quarantine.emit({"raw": row, "error": str(exc)})
'''
        result = anti_005_silent_drop_in_code(source_code=good_code)
        # No bare continue/pass after except, so no violation
        assert result is None

    def test_no_violation_on_non_parse_try_except(self):
        # try/except not involving parsing should not trigger
        unrelated_code = '''
def fetch():
    try:
        requests.get(url)
    except ConnectionError:
        pass
'''
        result = anti_005_silent_drop_in_code(source_code=unrelated_code)
        assert result is None


# ============================================================================
# ANTI-006: rate_limit_per_upstream
# ============================================================================


class TestAnti006RateLimit:

    def test_no_violation_under_limit(self):
        result = anti_006_rate_limit_exceeded(
            upstream_id="mes_server",
            declared_max_workers=3,
            declared_request_delay_s=0.15,
            attempted_concurrent_workers=2,
            attempted_request_delay_s=0.2,
        )
        assert result is None

    def test_violation_workers_exceeded(self):
        result = anti_006_rate_limit_exceeded(
            upstream_id="mes_server",
            declared_max_workers=3,
            declared_request_delay_s=0.15,
            attempted_concurrent_workers=10,
            attempted_request_delay_s=0.2,
        )
        assert result is not None
        assert result.name == "rate_limit_workers_exceeded"
        assert result.blocked is True

    def test_violation_delay_too_short(self):
        result = anti_006_rate_limit_exceeded(
            upstream_id="mes_server",
            declared_max_workers=3,
            declared_request_delay_s=0.15,
            attempted_concurrent_workers=3,
            attempted_request_delay_s=0.05,
        )
        assert result is not None
        assert result.name == "rate_limit_delay_too_short"


# ============================================================================
# ANTI-007: memory.append_only
# ============================================================================


class TestAnti007OriginalLayer:

    def test_violation_on_update_original_layer(self):
        result = anti_007_original_layer_modification(
            target_layer="original",
            action="update",
        )
        assert result is not None
        assert result.anti_id == "ANTI-007"
        assert result.blocked is True

    def test_violation_on_delete_original_layer(self):
        result = anti_007_original_layer_modification(
            target_layer="original",
            action="delete",
        )
        assert result is not None

    def test_no_violation_on_insert_original(self):
        # Insert into original is OK (append-only)
        result = anti_007_original_layer_modification(
            target_layer="original",
            action="insert",
        )
        assert result is None

    def test_no_violation_on_update_correction(self):
        result = anti_007_original_layer_modification(
            target_layer="correction",
            action="update",
        )
        assert result is None

    def test_no_violation_on_working_layer(self):
        result = anti_007_original_layer_modification(
            target_layer="working",
            action="delete",
        )
        assert result is None


# ============================================================================
# Catalog inventory
# ============================================================================


class TestCatalogInventory:

    def test_list_invariants_has_seven(self):
        invariants = list_invariants()
        assert len(invariants) == 7
        ids = [iid for iid, _ in invariants]
        assert ids == ["ANTI-001", "ANTI-002", "ANTI-003",
                        "ANTI-004", "ANTI-005", "ANTI-006",
                        "ANTI-007"]


# ============================================================================
# InvariantViolation -> audit envelope
# ============================================================================


class TestAuditEnvelope:

    def test_envelope_has_canonical_fields(self):
        v = anti_001_bulk_read_without_window(
            connector_shared_production=True,
            call_args={"log_code": "em-repair-wp1"},
        )
        env = v.to_audit_envelope(agent_id="claude", session_id="sess_1")
        assert env["event_type"]  # truthy
        assert env["anti_id"] == "ANTI-001"
        assert env["blocked"] is True
        assert env["agent_id"] == "claude"
        assert env["session_id"] == "sess_1"
        assert "ts_utc" in env
