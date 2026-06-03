from __future__ import annotations

from pathlib import Path

from waggledance.core.idle_consensus_charter import (
    DEFAULT_DAILY_QUOTA,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)


ROOT = Path(__file__).resolve().parents[2]


def test_charter_loads_from_default_path() -> None:
    charter = load_charter()
    assert charter.daily_quota == DEFAULT_DAILY_QUOTA
    assert len(charter.allowlist) >= 5
    assert len(charter.file_denylist) >= 5
    assert len(charter.code_pattern_denylist) >= 3
    assert len(charter.operator_quotes) >= 4


def test_charter_allowlist_contains_known_substrate_paths() -> None:
    charter = load_charter()
    assert "tools/**" in charter.allowlist
    assert "tests/**" in charter.allowlist
    assert "waggledance/core/magma/**" in charter.allowlist


def test_charter_denylist_contains_known_charter_paths() -> None:
    charter = load_charter()
    assert "CLAUDE.md" in charter.file_denylist
    assert "memory/**" in charter.file_denylist
    assert ".agent-bridge/bin/**" in charter.file_denylist
    assert "README.md" in charter.file_denylist
    assert "pyproject.toml" in charter.file_denylist
    assert "**/*secret*" in charter.file_denylist


def test_evaluate_paths_allows_substrate_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/run_idle_protocol_once.py"])
    assert decision.allowed is True
    assert decision.reason == "allowlist match, no denylist hit"


def test_evaluate_paths_blocks_denylisted_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["CLAUDE.md"])
    assert decision.allowed is False
    assert "CLAUDE.md" in decision.blocked_paths


def test_evaluate_paths_blocks_memory_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["memory/some_file.md"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("memory/some_file.md",)


def test_evaluate_paths_blocks_secret_like_allowlisted_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/secret_token.py"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("tools/secret_token.py",)


def test_evaluate_paths_blocks_secret_like_path_case_insensitively() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/SecretToken.py", "tools/API_TOKEN.py"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("tools/SecretToken.py", "tools/API_TOKEN.py")


def test_evaluate_paths_blocks_top_level_manual_review_paths() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["README.md", "pyproject.toml"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("README.md", "pyproject.toml")


def test_evaluate_paths_rejects_unmatched_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["some/unknown/location.py"],
    )
    assert decision.allowed is False
    assert "some/unknown/location.py" in decision.unmatched_paths


def test_evaluate_paths_handles_mixed_allow_and_deny() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["tools/run_idle_protocol_once.py", "CLAUDE.md"],
    )
    assert decision.allowed is False
    assert decision.blocked_paths == ("CLAUDE.md",)


def test_evaluate_paths_no_changes_refused() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, [])
    assert decision.allowed is False
    assert decision.reason == "no changed paths provided"


def test_evaluate_diff_content_blocks_auto_execute_constant() -> None:
    charter = load_charter()
    diff = "+ auto_execute=False\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert len(decision.code_pattern_hits) >= 1


def test_evaluate_diff_content_blocks_spaced_auto_execute_constant() -> None:
    charter = load_charter()
    diff = "+ auto_execute = False\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_second_gate_constant() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required=True\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_spaced_second_gate_constant() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required = True\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_second_sequence_marker() -> None:
    charter = load_charter()
    diff = "+ _sequence_errors = []\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_receipt_bundle_write_marker() -> None:
    charter = load_charter()
    diff = "- write_receipt_bundle(...)\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_private_marker() -> None:
    charter = load_charter()
    diff = "+ PRIVATE_MARKER = 'something'\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_second_privacy_marker() -> None:
    charter = load_charter()
    diff = "+ _DO_NOT_LEAK = 'secret-test-marker'\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_allows_substrate_diff() -> None:
    charter = load_charter()
    diff = "+ def new_helper():\n+     return 1"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_empty_allowed() -> None:
    charter = load_charter()
    decision = evaluate_diff_content(charter, "")
    assert decision.allowed is True


def test_operator_quotes_preserved() -> None:
    charter = load_charter()
    # Verify the operator quotes were extracted (Finnish text)
    quotes_text = " ".join(charter.operator_quotes)
    assert "automaattisen" in quotes_text or "autonominen" in quotes_text


def test_glob_path_pattern_matches_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["waggledance/core/magma/canonical.py"],
    )
    assert decision.allowed is True


def test_glob_path_pattern_matches_tests_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["tests/unit/test_something.py"],
    )
    assert decision.allowed is True
