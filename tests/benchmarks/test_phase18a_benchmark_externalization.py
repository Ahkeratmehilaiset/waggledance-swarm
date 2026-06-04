# SPDX-License-Identifier: BUSL-1.1
"""Phase 18A - Benchmark externalization + schema hardening tests.

Covers exporter and validator end-to-end against the committed Phase
17B / 17C / 17D source artifacts. Adversarial fixtures cover missing
files, checksum mismatch, unknown labels, raw stdout leakage, forbidden
substrings, provider-delta drift.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _load_exporter():
    name = "run_phase18a_benchmark_externalization"
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


def _load_validator():
    name = "validate_phase18a_benchmark_bundle"
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """Run the exporter once into tmp and return the bundle path."""
    exporter = _load_exporter()
    out_dir = tmp_path / "bundle"
    exporter.export_bundle(
        source_root=ROOT,
        out_dir=out_dir,
        include_raw=False,
        generated_at_utc="2026-05-05T06:30:00Z",
        git_sha="0" * 40,
        branch="phase18a/test",
    )
    return out_dir


# ---------------------------------------------------------------------------
# Schema files
# ---------------------------------------------------------------------------

def test_schemas_are_valid_json():
    schema_dir = ROOT / "schemas" / "benchmarks" / "v1"
    expected = (
        "benchmark_bundle.schema.json",
        "artifact_index.schema.json",
        "claim_evidence_ledger.schema.json",
        "release_lineage.schema.json",
        "local_efficiency.schema.json",
        "local_ollama_baseline.schema.json",
        "local_model_sweep.schema.json",
    )
    for name in expected:
        path = schema_dir / name
        assert path.is_file(), f"missing schema {name}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict)
        assert doc.get("$schema", "").startswith("https://json-schema.org/")
        assert doc.get("type") == "object"
        assert "required" in doc
        assert isinstance(doc["required"], list)


# ---------------------------------------------------------------------------
# Bundle layout + happy path
# ---------------------------------------------------------------------------

def test_exporter_writes_complete_bundle(bundle: Path):
    expected = (
        "benchmark_bundle_manifest.json",
        "artifact_index.json",
        "claim_evidence_ledger.json",
        "release_lineage.json",
        "checksums.sha256",
        "README.md",
        "reports/benchmark_bundle_index.md",
        "reports/claim_evidence_ledger.md",
        "schemas/benchmark_bundle.schema.json",
        "schemas/artifact_index.schema.json",
        "schemas/claim_evidence_ledger.schema.json",
        "schemas/release_lineage.schema.json",
        "schemas/local_efficiency.schema.json",
        "schemas/local_ollama_baseline.schema.json",
        "schemas/local_model_sweep.schema.json",
        "artifacts/phase17b_local_efficiency_benchmark.sanitized.json",
        "artifacts/phase17c_local_ollama_baseline.sanitized.json",
        "artifacts/phase17d_local_model_sweep.sanitized.json",
    )
    for rel in expected:
        assert (bundle / rel).is_file(), f"missing {rel}"


def test_validator_accepts_valid_bundle(bundle: Path):
    validator = _load_validator()
    ok, errors = validator.validate_bundle(bundle)
    assert ok, f"validator unexpectedly failed: {errors}"
    assert errors == []


def test_validator_freshness_gate_is_opt_in_for_historical_committed_bundle():
    validator = _load_validator()
    committed = (
        ROOT
        / "docs"
        / "runs"
        / "phase18a_benchmark_externalization_2026_05_05"
        / "export_bundle"
    )
    ok, errors = validator.validate_bundle(committed)
    assert ok, f"default validation should remain historical-compatible: {errors}"

    ok, errors = validator.validate_bundle(
        committed,
        max_age_days=14,
        now_utc=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )
    assert not ok
    assert any("max_age_days=14" in e for e in errors)


def test_validator_freshness_gate_accepts_exact_max_age_boundary(bundle: Path):
    validator = _load_validator()
    ok, errors = validator.validate_bundle(
        bundle,
        max_age_days=14,
        now_utc=datetime(2026, 5, 19, 6, 30, tzinfo=timezone.utc),
    )
    assert ok, f"exact max-age boundary should pass: {errors}"


def test_validator_freshness_gate_rejects_stale_bundle(bundle: Path):
    validator = _load_validator()
    ok, errors = validator.validate_bundle(
        bundle,
        max_age_days=14,
        now_utc=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )
    assert not ok
    assert any(
        "manifest.generated_at_utc age exceeds max_age_days" in e
        for e in errors
    )


def test_validator_freshness_gate_rejects_naive_now(bundle: Path):
    validator = _load_validator()
    ok, errors = validator.validate_bundle(
        bundle,
        max_age_days=14,
        now_utc=datetime(2026, 6, 4),
    )
    assert not ok
    assert "now_utc must include UTC offset or Z" in errors


def test_validator_freshness_gate_rejects_malformed_generated_at(bundle: Path):
    validator = _load_validator()
    manifest_path = bundle / "benchmark_bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at_utc"] = "2026-05-05 06:30:00"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _refresh_one_checksum(bundle, "benchmark_bundle_manifest.json")

    ok, errors = validator.validate_bundle(
        bundle,
        max_age_days=14,
        now_utc=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )
    assert not ok
    assert any(
        "manifest.generated_at_utc" in e and "timestamp" in e
        for e in errors
    )


def test_validator_cli_freshness_gate_rejects_stale_bundle(capsys):
    validator = _load_validator()
    committed = (
        ROOT
        / "docs"
        / "runs"
        / "phase18a_benchmark_externalization_2026_05_05"
        / "export_bundle"
    )
    exit_code = validator.main([
        "--bundle-dir",
        str(committed),
        "--max-age-days",
        "14",
        "--now",
        "2026-06-04T00:00:00Z",
    ])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Phase 18A bundle validation: FAIL" in out
    assert "max_age_days=14" in out


# ---------------------------------------------------------------------------
# Adversarial paths
# ---------------------------------------------------------------------------

def test_validator_rejects_missing_required_file(bundle: Path):
    validator = _load_validator()
    (bundle / "claim_evidence_ledger.json").unlink()
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("claim_evidence_ledger.json" in e for e in errors)


def test_validator_rejects_checksum_mismatch(bundle: Path):
    validator = _load_validator()
    artifact = bundle / "artifacts" / "phase17b_local_efficiency_benchmark.sanitized.json"
    raw = artifact.read_text(encoding="utf-8")
    artifact.write_text(raw + " ", encoding="utf-8")
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("mismatch" in e or "exported_sha256" in e for e in errors)


def test_validator_rejects_unknown_claim_label(bundle: Path):
    validator = _load_validator()
    ledger_path = bundle / "claim_evidence_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["claims"][0]["label"] = "FANTASTIC"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True),
                              encoding="utf-8")
    # Recompute checksum so checksum-pass clears and we hit label fail.
    _refresh_one_checksum(bundle, "claim_evidence_ledger.json")
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("FANTASTIC" in e or "label" in e.lower() for e in errors)


def test_validator_rejects_unresolved_evidence_reference(bundle: Path):
    validator = _load_validator()
    ledger_path = bundle / "claim_evidence_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["claims"][0]["evidence_path_in_bundle"] = "artifacts/nope.json"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True),
                              encoding="utf-8")
    _refresh_one_checksum(bundle, "claim_evidence_ledger.json")
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("nope.json" in e for e in errors)


def test_validator_rejects_artifact_path_escape(bundle: Path, tmp_path: Path):
    validator = _load_validator()
    outside = tmp_path / "outside.json"
    source = (
        bundle
        / "artifacts"
        / "phase17b_local_efficiency_benchmark.sanitized.json"
    )
    outside.write_bytes(source.read_bytes())

    artifact_index_path = bundle / "artifact_index.json"
    artifact_index = json.loads(artifact_index_path.read_text(encoding="utf-8"))
    artifact_index["artifacts"][0]["path_in_bundle"] = "../outside.json"
    artifact_index["artifacts"][0]["exported_sha256"] = validator.sha256_of_file(
        outside
    )
    artifact_index_path.write_text(
        json.dumps(artifact_index, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _refresh_one_checksum(bundle, "artifact_index.json")

    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any(
        "unsafe relative path" in e and "../outside.json" in e for e in errors
    )


def test_validator_rejects_checksum_path_escape(bundle: Path, tmp_path: Path):
    validator = _load_validator()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside evidence\n", encoding="utf-8")
    checksum = validator.sha256_of_file(outside)
    checksums_path = bundle / "checksums.sha256"
    checksums_path.write_text(
        checksums_path.read_text(encoding="utf-8")
        + f"{checksum}  ../outside.txt\n",
        encoding="utf-8",
    )

    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any(
        "unsafe relative path" in e and "../outside.txt" in e for e in errors
    )


def test_validator_rejects_raw_stdout_leakage(tmp_path: Path):
    """Re-export with --include-raw and assert the validator detects the
    leakage AND the exporter correctly sets release_gate_pass=false."""
    exporter = _load_exporter()
    validator = _load_validator()
    out_dir = tmp_path / "raw_bundle"
    manifest = exporter.export_bundle(
        source_root=ROOT,
        out_dir=out_dir,
        include_raw=True,
        generated_at_utc="2026-05-05T06:30:00Z",
        git_sha="0" * 40,
        branch="phase18a/test-raw",
    )
    assert manifest["release_gate_pass"] is False
    # The manifest schema requires release_gate_pass=true, so the
    # validator must reject this bundle on that ground (and possibly on
    # raw stdout too).
    ok, errors = validator.validate_bundle(out_dir)
    assert not ok
    # Either the manifest schema fails or the leakage scan fires.
    assert any(("release_gate_pass" in e) or ("leakage" in e) or ("stdout" in e)
                 for e in errors)


def test_validator_rejects_unsupported_ranking_claim(bundle: Path):
    validator = _load_validator()
    ledger_md = bundle / "reports" / "claim_evidence_ledger.md"
    ledger_md.write_text(
        ledger_md.read_text(encoding="utf-8")
        + "\n[INJECTED] gemma is faster than llama.\n",
        encoding="utf-8",
    )
    _refresh_one_checksum(bundle, "reports/claim_evidence_ledger.md")
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("forbidden substring" in e for e in errors)


def test_validator_rejects_provider_delta_nonzero(bundle: Path):
    validator = _load_validator()
    manifest_path = bundle / "benchmark_bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider_jobs_delta"] = 1
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    _refresh_one_checksum(bundle, "benchmark_bundle_manifest.json")
    ok, errors = validator.validate_bundle(bundle)
    assert not ok
    assert any("provider_jobs_delta" in e for e in errors)


# ---------------------------------------------------------------------------
# Source SHA-256 integrity, required claims, lineage shape
# ---------------------------------------------------------------------------

def test_exporter_preserves_source_sha256(bundle: Path):
    artifact_index = json.loads(
        (bundle / "artifact_index.json").read_text(encoding="utf-8")
    )
    for entry in artifact_index["artifacts"]:
        src = ROOT / entry["source_path_in_repo"]
        assert src.is_file(), f"source missing: {src}"
        actual = hashlib.sha256(src.read_bytes()).hexdigest()
        assert entry["source_sha256"] == actual


def test_claim_ledger_includes_required_claims(bundle: Path):
    ledger = json.loads(
        (bundle / "claim_evidence_ledger.json").read_text(encoding="utf-8")
    )
    seen = {c["claim_id"] for c in ledger["claims"]}
    expected = {
        "docker_offline_proven",
        "producer_fabric_proven",
        "capability_lookup_10k_measured",
        "canonical_corpus_128_proven",
        "local_efficiency_harness_proven",
        "local_ollama_one_model_measured",
        "local_ollama_panel_measured",
        "raw_intelligence_vs_frontier_moe_not_claimed",
        "cross_vendor_ranking_not_claimed",
        "no_model_pull_or_download",
        "no_cloud_api_calls",
        "provider_builder_delta_zero",
        "no_stage2_flip",
        "no_human_approval_collected",
        "no_allowlist_widening",
        "benchmark_artifact_externalization",
    }
    missing = expected - seen
    assert not missing, f"missing required claim_ids: {missing}"


def test_release_lineage_shape(bundle: Path):
    lineage = json.loads(
        (bundle / "release_lineage.json").read_text(encoding="utf-8")
    )
    sl = lineage["stable_latest"]
    assert sl["tag"] == "v3.8.0"
    assert sl["isPrerelease"] is False
    assert sl["is_github_latest"] is True
    assert sl["target_sha"] == "824176ebf2a6b8debed41982090a125cbe2ddad1"
    pre_tags = {p["tag"] for p in lineage["prereleases"]}
    expected = {
        "v3.9.0-producer-fabric-alpha",
        "v3.9.1-local-efficiency-benchmark-alpha",
        "v3.9.2-local-ollama-baseline-alpha",
        "v3.9.3-local-model-sweep-alpha",
    }
    assert expected.issubset(pre_tags)
    assert lineage["candidate"]["tag"] == "v3.10.0-benchmark-schema-alpha"
    assert lineage["candidate"]["expected_isPrerelease"] is True


# ---------------------------------------------------------------------------
# Markdown reports
# ---------------------------------------------------------------------------

def test_markdown_reports_generated(bundle: Path):
    idx = (bundle / "reports" / "benchmark_bundle_index.md").read_text(
        encoding="utf-8"
    )
    led = (bundle / "reports" / "claim_evidence_ledger.md").read_text(
        encoding="utf-8"
    )
    assert "Phase 18A" in idx
    assert "phase17b_local_efficiency_benchmark" in idx
    assert "phase17c_local_ollama_baseline" in idx
    assert "phase17d_local_model_sweep" in idx
    assert "Phase 18A" in led
    assert "MEASURED_LOCAL_OLLAMA_PANEL" in led
    assert "NOT_CLAIMED" in led
    # Must NOT contain raw forbidden substrings.
    lower = (idx + "\n" + led).lower()
    for word in ("is faster than", "outperforms", "agi",
                  "beats all competitors"):
        assert word not in lower, f"forbidden substring '{word}'"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_deterministic_export(tmp_path: Path):
    exporter = _load_exporter()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    pinned_utc = "2026-05-05T06:30:00Z"
    pinned_sha = "1" * 40
    exporter.export_bundle(source_root=ROOT, out_dir=out_a,
                              include_raw=False,
                              generated_at_utc=pinned_utc,
                              git_sha=pinned_sha, branch="x")
    exporter.export_bundle(source_root=ROOT, out_dir=out_b,
                              include_raw=False,
                              generated_at_utc=pinned_utc,
                              git_sha=pinned_sha, branch="x")
    # Compare byte-equal of artifacts, schemas, ledger, lineage, manifest.
    targets = (
        "benchmark_bundle_manifest.json",
        "artifact_index.json",
        "claim_evidence_ledger.json",
        "release_lineage.json",
        "checksums.sha256",
        "README.md",
        "reports/benchmark_bundle_index.md",
        "reports/claim_evidence_ledger.md",
        "schemas/benchmark_bundle.schema.json",
        "artifacts/phase17b_local_efficiency_benchmark.sanitized.json",
        "artifacts/phase17c_local_ollama_baseline.sanitized.json",
        "artifacts/phase17d_local_model_sweep.sanitized.json",
    )
    for t in targets:
        a = (out_a / t).read_bytes()
        b = (out_b / t).read_bytes()
        assert a == b, f"non-deterministic file: {t}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refresh_one_checksum(bundle: Path, relpath: str) -> None:
    """After an adversarial mutation that should be detected by a non-
    checksum check, refresh that one file's checksum so the validator
    reaches the intended check instead of failing on checksum.

    Note: NOT used for the checksum-mismatch test - that one needs the
    original checksum to remain stale on purpose.
    """
    target_path = bundle / relpath
    sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
    sums = (bundle / "checksums.sha256").read_text(encoding="utf-8")
    new_lines = []
    for line in sums.splitlines():
        if line.endswith(f"  {relpath}"):
            new_lines.append(f"{sha}  {relpath}")
        else:
            new_lines.append(line)
    (bundle / "checksums.sha256").write_text(
        "\n".join(new_lines) + "\n", encoding="utf-8"
    )
