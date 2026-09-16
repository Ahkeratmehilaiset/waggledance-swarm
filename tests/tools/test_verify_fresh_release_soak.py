"""Synthetic Git/log fixtures; these tests are never runtime soak evidence."""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.release_soak_log_attestation import (
    FRESH_COVERAGE_SOURCE,
    FRESH_SOURCE_ROLES,
    evaluate_soak_log_source_attestation,
)
from tools.run_release_soak_log_audit import build_bound_report


START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
LOCK = b"fixture-only==1.0\n"
LOCK_DIGEST = "sha256:" + hashlib.sha256(LOCK).hexdigest()


def git(root, *args):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, env=env, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def subject(tmp_path):
    root = tmp_path / "synthetic-repo"
    root.mkdir()
    git(root, "init", "-q")
    for key, value in (
        ("user.name", "Synthetic fixture"), ("user.email", "fixture@example.invalid"),
        ("commit.gpgsign", "false"), ("core.autocrlf", "false"),
    ):
        git(root, "config", key, value)
    (root / "requirements.lock.txt").write_bytes(LOCK)
    for relative in FRESH_SOURCE_ROLES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"" if relative == FRESH_COVERAGE_SOURCE else
                         b'{"ts_utc":"2025-01-01T00:00:00Z","message":"fixture"}\n')
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic subject, not runtime evidence")
    commit = git(root, "rev-parse", "HEAD")
    return root, commit


def make_report(subject, *, hours=336, step=12):
    root, commit = subject
    instants = sorted(set([*range(0, hours + 1, step), hours]))
    records = [{
        "kind": "soak_heartbeat", "state": "ok", "source_commit": commit,
        "lock_digest": LOCK_DIGEST, "seq": index,
        "ts_utc": (START + dt.timedelta(hours=hour)).isoformat(),
    } for index, hour in enumerate(instants)]
    (root / FRESH_COVERAGE_SOURCE).write_bytes(
        b"".join((json.dumps(row) + "\n").encode() for row in records)
    )
    report = build_bound_report(
        [Path(relative) for relative in FRESH_SOURCE_ROLES], source_root=root,
        source_commit=commit, coverage_sources=[Path(FRESH_COVERAGE_SOURCE)],
        started_at_utc=START, ended_at_utc=START + dt.timedelta(hours=hours),
        generated_at=START + dt.timedelta(hours=hours, minutes=1),
        required_window_hours=hours,
    )
    assert report["audit_result"] == "pass", report["blockers"]
    path = root.parent / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def evaluate(subject, path):
    from tools.verify_fresh_release_soak import evaluate_fresh_release_soak
    root, commit = subject
    return evaluate_fresh_release_soak(path, root, commit)


def rewrite(path, report):
    path.write_text(json.dumps(report), encoding="utf-8")


def test_generic_helpers_allow_short_policy_but_release_consumer_must_not(subject):
    path, _ = make_report(subject, hours=1)
    assert evaluate_soak_log_source_attestation(
        path, *subject, required_window_hours=1, require_fresh_contract=True,
    ) == []
    result = evaluate(subject, path)
    assert result["decision"] == "hold"
    assert "soak_log_window_invalid" in result["blockers"]


def test_full_synthetic_window_passes_offline_only(subject):
    path, _ = make_report(subject)
    before = path.read_bytes()
    result = evaluate(subject, path)
    assert result["decision"] == "pass", result
    assert result["release_authorized"] is False
    assert result["runtime_elapsed_proven"] is False
    assert result["proof_scope"] == "offline_fresh_soak_snapshot"
    assert result["required_window_hours"] == 336
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("source_tree", "a" * 40), ("lock_blob", "a" * 40),
    ("raw_log_binding", {}), ("coverage", {}), ("worktree", {}),
    ("window_hours", 999), ("source_commit", "a" * 40),
    ("target_version", "v3.13.0"), ("required_window_hours", 1),
    ("required_window_hours", True), ("max_gap_hours", 48),
    ("max_gap_hours", True),
    ("proof_scope", "verified_elapsed_runtime"),
    ("runtime_elapsed_proven", True), ("runtime_elapsed_proven", 0),
    ("release_authorized", True), ("release_authorized", 0),
])
def test_forged_or_weakened_report_holds(subject, field, value):
    path, report = make_report(subject)
    report[field] = value
    rewrite(path, report)
    assert evaluate(subject, path)["decision"] == "hold"


def test_source_change_after_report_holds(subject):
    path, _ = make_report(subject)
    (subject[0] / "requirements.lock.txt").write_bytes(b"changed==2\n")
    assert evaluate(subject, path)["decision"] == "hold"


def test_future_generation_is_not_fresh_evidence(subject):
    path, report = make_report(subject)
    report["generated_at"] = "2999-01-01T00:00:00Z"
    rewrite(path, report)
    assert "soak_consumer_time_invalid" in evaluate(subject, path)["blockers"]


@pytest.mark.parametrize("content", [
    b"[]", b"null", b"{", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff",
])
def test_malformed_report_fails_closed(subject, content):
    path = subject[0].parent / "invalid.json"
    path.write_bytes(content)
    assert evaluate(subject, path)["decision"] == "hold"


def test_absent_report_fails_closed(subject):
    assert evaluate(subject, subject[0].parent / "missing.json")["decision"] == "hold"


def test_hardlinked_report_is_rejected(subject):
    path, _ = make_report(subject)
    alias = path.with_name("alias.json")
    try:
        os.link(path, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    assert evaluate(subject, alias)["decision"] == "hold"


def test_report_change_during_validation_holds(subject, monkeypatch):
    import tools.verify_fresh_release_soak as consumer
    path, _ = make_report(subject)
    original = consumer.build_bound_report

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(consumer, "build_bound_report", changed)
    assert "soak_consumer_snapshot_changed" in evaluate(subject, path)["blockers"]


def test_rebuilder_failure_is_not_a_pass(subject, monkeypatch):
    import tools.verify_fresh_release_soak as consumer
    path, _ = make_report(subject)

    def broken(*args, **kwargs):
        raise ValueError("untrusted diagnostic, not a public blocker")

    monkeypatch.setattr(consumer, "build_bound_report", broken)
    result = evaluate(subject, path)
    assert result["decision"] == "hold"
    assert "soak_consumer_verification_failed" in result["blockers"]


def test_cli_cannot_override_release_duration(subject, capsys):
    from tools.verify_fresh_release_soak import main
    path, _ = make_report(subject)
    argv = ["--report", str(path), "--source-root", str(subject[0]),
            "--expected-commit", subject[1]]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["release_authorized"] is False
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--required-window-hours", "1"])
    assert exc.value.code == 2
