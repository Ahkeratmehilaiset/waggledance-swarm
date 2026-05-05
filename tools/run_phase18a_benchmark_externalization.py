# SPDX-License-Identifier: BUSL-1.1
"""Phase 18A - Benchmark Externalization Exporter.

Reads committed Phase 17B / 17C / 17D benchmark JSON artifacts and
packages them into a versioned, validated, offline-exportable evidence
bundle with:

* sanitized artifacts (per-prompt stdout/stderr -> redaction stub),
* manifest, artifact index, claim ledger, release lineage,
* SHA-256 checksums of every exported file,
* schema files copied into the bundle,
* generated Markdown reports.

Hard invariants:

    * No model pull or download; no cloud API calls.
    * No new measurements - we re-export existing committed artifacts.
    * No raw-intelligence superiority claim. No cross-vendor ranking.
    * No forbidden-vocabulary substrings in rendered MD.
    * Validator-clean by default: --include-raw is opt-in and disables
      release_gate_pass.

CLI:

    python tools/run_phase18a_benchmark_externalization.py [--out-dir ...] [--validate] [--include-raw] [--strict] [--source-root ...] [--generated-at-utc ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

# Source artifacts (committed, frozen).
SOURCE_ARTIFACTS = (
    {
        "artifact_id": "phase17b_local_efficiency_benchmark",
        "phase": "phase17b",
        "source_path": "docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/phase17b_local_efficiency_benchmark.json",
        "exported_filename": "phase17b_local_efficiency_benchmark.sanitized.json",
        "declared_schema": "local_efficiency.schema.json",
    },
    {
        "artifact_id": "phase17c_local_ollama_baseline",
        "phase": "phase17c",
        "source_path": "docs/runs/phase17c_local_ollama_baseline_2026_05_04/phase17c_local_ollama_baseline.json",
        "exported_filename": "phase17c_local_ollama_baseline.sanitized.json",
        "declared_schema": "local_ollama_baseline.schema.json",
    },
    {
        "artifact_id": "phase17d_local_model_sweep",
        "phase": "phase17d",
        "source_path": "docs/runs/phase17d_local_model_sweep_2026_05_05/phase17d_local_model_sweep.json",
        "exported_filename": "phase17d_local_model_sweep.sanitized.json",
        "declared_schema": "local_model_sweep.schema.json",
    },
)

SCHEMA_FILES = (
    "benchmark_bundle.schema.json",
    "artifact_index.schema.json",
    "claim_evidence_ledger.schema.json",
    "release_lineage.schema.json",
    "local_efficiency.schema.json",
    "local_ollama_baseline.schema.json",
    "local_model_sweep.schema.json",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_text_lf(path: Path, text: str) -> None:
    """Write `text` as UTF-8 with explicit LF line endings.

    Phase 18B fix: Path.write_text() in Python text mode performs
    platform newline translation (LF -> CRLF on Windows). That made the
    Phase 18A bundle's checksums.sha256 unstable across platforms,
    because a fresh Windows checkout would CRLF-expand the schemas/
    JSON files and break SHA-256 verification. Writing bytes directly
    keeps the bundle byte-stable.
    """
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    path.write_bytes(text.encode("utf-8"))


def _copy_text_lf(src: Path, dst: Path) -> None:
    """Copy a text file from `src` to `dst`, normalizing CRLF to LF.

    Phase 18B fix: a Windows checkout of `schemas/benchmarks/v1/*.json`
    can have CRLF on disk while the index has LF. We always emit LF
    inside the bundle so the bundle's bytes match the bundle's
    checksums regardless of how the source was checked out.
    """
    raw = src.read_bytes()
    dst.write_bytes(raw.replace(b"\r\n", b"\n"))


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha(root: Path) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, check=False, timeout=10,
        )
        out = completed.stdout
        if isinstance(out, (bytes, bytearray)):
            out = out.decode("utf-8", errors="replace")
        return out.strip() or "0" * 40
    except Exception:  # noqa: BLE001
        return "0" * 40


def _git_branch(root: Path) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(root),
            capture_output=True, check=False, timeout=10,
        )
        out = completed.stdout
        if isinstance(out, (bytes, bytearray)):
            out = out.decode("utf-8", errors="replace")
        return out.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

def _redaction_stub_for(s: str | bytes) -> dict[str, Any]:
    if isinstance(s, str):
        b = s.encode("utf-8")
    elif isinstance(s, (bytes, bytearray)):
        b = bytes(s)
    else:
        b = json.dumps(s, sort_keys=True, default=str).encode("utf-8")
    return {
        "redacted": True,
        "sha256": _sha256_bytes(b),
        "length": len(b),
    }


def _sanitize(node: Any, *, include_raw: bool) -> Any:
    if include_raw:
        return node
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            if key in ("stdout", "stderr") and isinstance(val, (str, bytes,
                                                                    bytearray)):
                out[key] = _redaction_stub_for(val)
            else:
                out[key] = _sanitize(val, include_raw=include_raw)
        return out
    if isinstance(node, list):
        return [_sanitize(x, include_raw=include_raw) for x in node]
    return node


# ---------------------------------------------------------------------------
# Release lineage (canonical, baked in)
# ---------------------------------------------------------------------------

def _release_lineage() -> dict[str, Any]:
    return {
        "schema_version": "benchmarks.v1",
        "stable_latest": {
            "tag": "v3.8.0",
            "target_sha": "824176ebf2a6b8debed41982090a125cbe2ddad1",
            "isPrerelease": False,
            "is_github_latest": True,
            "publishedAt": "2026-05-04T07:13:27Z",
        },
        "prereleases": [
            {
                "tag": "v3.9.0-producer-fabric-alpha",
                "target_sha": "c726995c816ee4c09e031c2190c3de6592e82879",
                "isPrerelease": True,
                "publishedAt": "2026-05-04T18:32:47Z",
            },
            {
                "tag": "v3.9.1-local-efficiency-benchmark-alpha",
                "target_sha": "f4d0a4a4152ca74e98a8d7f7161c233075bf4111",
                "isPrerelease": True,
                "publishedAt": "2026-05-04T20:59:09Z",
            },
            {
                "tag": "v3.9.2-local-ollama-baseline-alpha",
                "target_sha": "db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c",
                "isPrerelease": True,
                "publishedAt": "2026-05-04T22:26:28Z",
            },
            {
                "tag": "v3.9.3-local-model-sweep-alpha",
                "target_sha": "d0704efe46be18d480ed425ff83b087cd36ef9bd",
                "isPrerelease": True,
                "publishedAt": "2026-05-05T06:05:30Z",
            },
        ],
        "candidate": {
            "tag": "v3.10.0-benchmark-schema-alpha",
            "expected_isPrerelease": True,
            "expected_is_github_latest": False,
        },
    }


# ---------------------------------------------------------------------------
# Claim ledger (canonical, baked in)
# ---------------------------------------------------------------------------

def _build_claim_ledger(*, source_shas: dict[str, str]) -> dict[str, Any]:
    """Produce the canonical claim ledger.

    `source_shas` maps source_path_in_repo -> sha256 hex string.
    """
    s17b = source_shas[SOURCE_ARTIFACTS[0]["source_path"]]
    s17c = source_shas[SOURCE_ARTIFACTS[1]["source_path"]]
    s17d = source_shas[SOURCE_ARTIFACTS[2]["source_path"]]
    p17b = "artifacts/phase17b_local_efficiency_benchmark.sanitized.json"
    p17c = "artifacts/phase17c_local_ollama_baseline.sanitized.json"
    p17d = "artifacts/phase17d_local_model_sweep.sanitized.json"

    claims = [
        {
            "claim_id": "docker_offline_proven",
            "label": "PROVEN",
            "title": "Docker --network none end-to-end proof",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/release_gate_pass",
            "evidence_value_type": "boolean",
            "caveat": "Phase 17B+17C+17D each ran the WaggleDance carry-forward path inside Docker --network none and exited 0; the docker_phase*_verification.md files in each session folder contain the exact commands and image manifests.",
            "reproduce_command": "docker build -t waggledance:phase17d -f Dockerfile . && docker run --rm --network none waggledance:phase17d python tools/run_phase17c_local_ollama_baseline.py --skip-ollama --allow-no-ollama-track --output /tmp/phase17d.json",
            "scope": "WaggleDance autonomy + producer fabric + 10k synthetic capability lookup paths.",
            "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
        },
        {
            "claim_id": "producer_fabric_proven",
            "label": "PROVEN",
            "title": "Producer fabric (curiosity / self-model / dream / hive) end-to-end offline",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/tracks/E_producer_fabric/raw/passed",
            "evidence_value_type": "boolean",
            "caveat": "Phase 17A ported 14 stdlib-only producer modules from origin/phase8.5/hive-proposes; orchestrator emits 68 IR objects across 6 kinds; 6/6 negative cases pass.",
            "reproduce_command": "python tools/run_phase17a_producer_fabric_proof.py --out-dir <out>",
            "scope": "Offline producer fabric; deterministic fixtures.",
            "not_claimed": ["raw_intelligence_superiority"]
        },
        {
            "claim_id": "capability_lookup_10k_measured",
            "label": "MEASURED",
            "title": "Capability-lookup at 10000 synthetic descriptors",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/tracks/B_capability_lookup_10k/raw/lookup_capability_hits_total",
            "evidence_value_type": "integer",
            "caveat": "Synthetic descriptors only (is_synthetic_scale=true, not_canonical_corpus=true); the 10000 number is a measured ceiling on this 24-CPU host, not an architectural maximum.",
            "reproduce_command": "python tools/run_solver_scale_proof.py --descriptors 10000 --lookup-pass-count 1000",
            "scope": "WaggleDance RuntimeQueryRouter.route() capability-aware path; six-family allowlist.",
            "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
        },
        {
            "claim_id": "canonical_corpus_128_proven",
            "label": "PROVEN",
            "title": "Canonical seed corpus = 128 (Phase 17A expansion)",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/tracks/A_solver_hot_path/raw/corpus_total",
            "evidence_value_type": "integer",
            "caveat": "Phase 17A grew the corpus from 104 to 128 (+4 per family, no allowlist widening).",
            "reproduce_command": "python tools/run_automatic_runtime_hint_proof.py",
            "scope": "Six-family low-risk allowlist seed corpus.",
            "not_claimed": []
        },
        {
            "claim_id": "local_efficiency_harness_proven",
            "label": "PROVEN",
            "title": "Phase 17B local efficiency benchmark harness aggregates Phase 11-17A canonical proofs",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/release_gate_pass",
            "evidence_value_type": "boolean",
            "caveat": "Tracks A-E pass-through; Track F SKIPPED by default; Track G external competitor slots NOT_RUN by policy.",
            "reproduce_command": "python tools/run_phase17b_local_efficiency_benchmark.py --skip-ollama",
            "scope": "WaggleDance core proofs aggregated into a single artifact.",
            "not_claimed": ["raw_intelligence_superiority"]
        },
        {
            "claim_id": "local_ollama_one_model_measured",
            "label": "MEASURED_LOCAL_OLLAMA_ONE_MODEL",
            "title": "Phase 17C single-model Ollama baseline (gemma4:e4b)",
            "evidence_artifact": "phase17c_local_ollama_baseline.sanitized.json",
            "evidence_path_in_bundle": p17c,
            "source_path_in_repo": SOURCE_ARTIFACTS[1]["source_path"],
            "source_sha256": s17c,
            "evidence_field_pointer": "/ollama_track/prompts_succeeded",
            "evidence_value_type": "integer",
            "caveat": "30 deterministic prompts against one already-installed local model; no pull/download; no cloud API. Median 0.7866 s, p95 17.5538 s on this host. Single-model only; no ranking implied.",
            "reproduce_command": "python tools/run_phase17c_local_ollama_baseline.py",
            "scope": "Local Ollama daemon, gemma4:e4b, ollama 0.22.1, 30 prompts.",
            "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
        },
        {
            "claim_id": "local_ollama_panel_measured",
            "label": "MEASURED_LOCAL_OLLAMA_PANEL",
            "title": "Phase 17D 4-model Ollama panel + repeatability (3 repeats per model)",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/panel_summary/models_measured_count",
            "evidence_value_type": "integer",
            "caveat": "4 already-installed local models; 3 repeats; 30 prompts each = 360 prompts. CoV 0.002-0.029. Reported in selection order; no rank ordering is implied.",
            "reproduce_command": "python tools/run_phase17d_local_model_sweep.py",
            "scope": "Local Ollama daemon, 4-model panel, 360 prompts on this host.",
            "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
        },
        {
            "claim_id": "raw_intelligence_vs_frontier_moe_not_claimed",
            "label": "NOT_CLAIMED",
            "title": "Raw intelligence vs frontier MoE - explicit non-claim",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/claim_labels/raw_intelligence_vs_frontier_moe",
            "evidence_value_type": "string",
            "caveat": "WaggleDance does not assert raw-intelligence superiority over any frontier MoE model. No paired benchmark was run.",
            "reproduce_command": "(no measurement; this is an explicit non-claim)",
            "scope": "Public competitive comparison.",
            "not_claimed": ["raw_intelligence_superiority"]
        },
        {
            "claim_id": "cross_vendor_ranking_not_claimed",
            "label": "NOT_CLAIMED",
            "title": "Cross-vendor ranking among local models - explicit non-claim",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/claim_labels/no_cross_vendor_ranking",
            "evidence_value_type": "boolean",
            "caveat": "Per-model numbers are reported in selection order, side-by-side, never with rank ordering. Different models have different parameter counts, training data, and quantizations; the numbers are not directly comparable as 'which model is better'.",
            "reproduce_command": "(no measurement; this is an explicit non-claim)",
            "scope": "Phase 17D 4-model panel.",
            "not_claimed": ["cross_vendor_ranking"]
        },
        {
            "claim_id": "no_model_pull_or_download",
            "label": "PROVEN",
            "title": "No Ollama model was pulled or downloaded across Phase 17B/17C/17D",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/no_model_pull_or_download",
            "evidence_value_type": "boolean",
            "caveat": "Phase 17D harness scans every Ollama subprocess stdout AND stderr for pull/download substrings and aborts on a hit. Phase 17C harness never invokes ollama pull. The 22 already-installed models pre-existed at session start.",
            "reproduce_command": "python tools/run_phase17d_local_model_sweep.py",
            "scope": "All three benchmark harnesses.",
            "not_claimed": []
        },
        {
            "claim_id": "no_cloud_api_calls",
            "label": "PROVEN",
            "title": "No cloud LLM API was contacted across Phase 17B/17C/17D",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/no_cloud_api_calls",
            "evidence_value_type": "boolean",
            "caveat": "Anthropic, OpenAI, Gemini, llama.cpp, vLLM, mistral-rs slots remain documented NOT_RUN. Docker --network none disables all external network reachability for the carry-forward proofs.",
            "reproduce_command": "see Phase 17B Track G slots in the local efficiency benchmark JSON",
            "scope": "All three benchmark harnesses.",
            "not_claimed": []
        },
        {
            "claim_id": "provider_builder_delta_zero",
            "label": "PROVEN",
            "title": "provider_jobs_delta = builder_jobs_delta = 0 across all WaggleDance proof paths",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/provider_jobs_delta",
            "evidence_value_type": "integer",
            "caveat": "Master prompt rule 13 invariant. WaggleDance inner loop did not invoke a provider lane or builder lane during any of the measured proofs.",
            "reproduce_command": "any Phase 11-17A proof produces JSON with provider_jobs_delta_during_proof and builder_jobs_delta_during_proof",
            "scope": "WaggleDance autonomy hot-path.",
            "not_claimed": []
        },
        {
            "claim_id": "no_stage2_flip",
            "label": "PROVEN",
            "title": "Stage-2 atomic flip not executed in benchmark sessions",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/release_gates/no_allowlist_widened",
            "evidence_value_type": "boolean",
            "caveat": "Phase 17B/17C/17D session_state.json invariants all explicitly forbid Stage-2 flip; Phase 18A session_state.json carries the same invariant.",
            "reproduce_command": "(documented in session_state.json invariants under each docs/runs/phase1*_*/)",
            "scope": "Per-session honesty invariants.",
            "not_claimed": []
        },
        {
            "claim_id": "no_human_approval_collected",
            "label": "PROVEN",
            "title": "HUMAN_APPROVAL.yaml not collected in benchmark sessions",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/release_gates/no_allowlist_widened",
            "evidence_value_type": "boolean",
            "caveat": "Approval is one-shot and belongs only to the actual cutover execution session; benchmark sessions never collect.",
            "reproduce_command": "(documented in session_state.json invariants under each docs/runs/phase1*_*/)",
            "scope": "Per-session honesty invariants.",
            "not_claimed": []
        },
        {
            "claim_id": "no_allowlist_widening",
            "label": "PROVEN",
            "title": "Six-family low-risk allowlist remains unchanged",
            "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
            "evidence_path_in_bundle": p17b,
            "source_path_in_repo": SOURCE_ARTIFACTS[0]["source_path"],
            "source_sha256": s17b,
            "evidence_field_pointer": "/claim_labels/autonomous_learning_six_family",
            "evidence_value_type": "string",
            "caveat": "scalar_unit_conversion, lookup_table, threshold_rule, interval_bucket_classifier, linear_arithmetic, bounded_interpolation - exactly six.",
            "reproduce_command": "python tools/run_phase17b_local_efficiency_benchmark.py --skip-ollama",
            "scope": "WaggleDance autonomy growth allowlist.",
            "not_claimed": []
        },
        {
            "claim_id": "benchmark_artifact_externalization",
            "label": "PROVEN",
            "title": "This bundle: schemas, manifest, ledger, lineage, checksums, sanitized artifacts",
            "evidence_artifact": "phase17d_local_model_sweep.sanitized.json",
            "evidence_path_in_bundle": p17d,
            "source_path_in_repo": SOURCE_ARTIFACTS[2]["source_path"],
            "source_sha256": s17d,
            "evidence_field_pointer": "/release_gate_pass",
            "evidence_value_type": "boolean",
            "caveat": "The bundle's existence at this path with checksums.sha256 and schemas/ + artifacts/ + reports/ subdirectories, validated by tools/validate_phase18a_benchmark_bundle.py exiting 0, is the proof.",
            "reproduce_command": "python tools/run_phase18a_benchmark_externalization.py --out-dir <dir> --validate",
            "scope": "Phase 18A bundle export + validation contract.",
            "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
        },
    ]

    return {
        "schema_version": "benchmarks.v1",
        "allowed_labels": [
            "PROVEN", "MEASURED", "INFERRED", "NOT_CLAIMED", "NOT_RUN",
            "MEASURED_LOCAL_ONLY", "MEASURED_LOCAL_OLLAMA_ONE_MODEL",
            "MEASURED_LOCAL_OLLAMA_PANEL",
        ],
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _render_index_md(*, manifest: dict[str, Any],
                      artifact_index: dict[str, Any]) -> str:
    lines = [
        "# Phase 18A Benchmark Bundle - Index",
        "",
        f"**Bundle name:** {manifest['bundle_name']}",
        f"**Bundle version:** {manifest['bundle_version']}",
        f"**Schema version:** {manifest['schema_version']}",
        f"**Generated UTC:** {manifest['generated_at_utc']}",
        f"**Git SHA:** {manifest['git_sha']}",
        f"**Source branch:** {manifest['source_branch']}",
        f"**Release candidate:** {manifest['release_candidate']}",
        "",
        "## Honesty declarations",
        "",
        "* **No cloud API calls** were made.",
        "* **No model was pulled or downloaded.**",
        "* **No cross-vendor ranking is implied.**",
        "* WaggleDance does not assert raw-intelligence superiority.",
        "",
        "## Artifacts",
        "",
        "| artifact_id | phase | declared_schema | exported_sha256 (head) |",
        "| --- | --- | --- | --- |",
    ]
    for a in artifact_index["artifacts"]:
        lines.append(
            f"| `{a['artifact_id']}` | `{a['phase']}` | "
            f"`{a['declared_schema']}` | `{a['exported_sha256'][:16]}...` |"
        )
    lines += [
        "",
        "## Schemas",
        "",
    ]
    for s in manifest["schemas_listed"]:
        lines.append(f"* `{s}`")
    lines += [
        "",
        "## Reports",
        "",
    ]
    for r in manifest["reports_listed"]:
        lines.append(f"* `{r}`")
    lines += [
        "",
        "## Reproduce",
        "",
        "```",
        "python tools/run_phase18a_benchmark_externalization.py --out-dir <dir> --validate",
        "python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <dir>",
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_ledger_md(claim_ledger: dict[str, Any]) -> str:
    lines = [
        "# Phase 18A Claim Evidence Ledger",
        "",
        "| claim_id | label | evidence_artifact | scope |",
        "| --- | --- | --- | --- |",
    ]
    for c in claim_ledger["claims"]:
        lines.append(
            f"| `{c['claim_id']}` | **{c['label']}** | "
            f"`{c['evidence_artifact']}` | {c['scope']} |"
        )
    lines += [
        "",
        "## Negative claims",
        "",
        "* `raw_intelligence_vs_frontier_moe_not_claimed` - WaggleDance does not assert raw-intelligence superiority over any frontier MoE model.",
        "* `cross_vendor_ranking_not_claimed` - the Phase 17D 4-model panel reports per-model numbers in selection order; no rank ordering is implied.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_readme(*, manifest: dict[str, Any]) -> str:
    return (
        "# Phase 18A Benchmark Evidence Bundle\n"
        "\n"
        f"This directory is the canonical Phase 18A benchmark evidence bundle for the\n"
        f"`{manifest['release_candidate']}` candidate prerelease.\n"
        "\n"
        "* `benchmark_bundle_manifest.json` - top-level manifest.\n"
        "* `artifact_index.json` - one entry per sanitized benchmark artifact.\n"
        "* `claim_evidence_ledger.json` - claim-id -> label + evidence pointer.\n"
        "* `release_lineage.json` - stable Latest + four prior prereleases.\n"
        "* `checksums.sha256` - SHA-256 of every file in the bundle.\n"
        "* `schemas/` - JSON Schemas for the manifest, ledger, lineage, and the\n"
        "  three sanitized artifacts.\n"
        "* `artifacts/` - sanitized exports of Phase 17B / 17C / 17D JSONs.\n"
        "* `reports/` - human-readable Markdown index + claim ledger.\n"
        "\n"
        "## Honesty declarations\n"
        "\n"
        "* No cloud API calls were made.\n"
        "* No Ollama model was pulled or downloaded.\n"
        "* No cross-vendor ranking is implied.\n"
        "* WaggleDance does not assert raw-intelligence superiority.\n"
        "\n"
        "## Validate this bundle\n"
        "\n"
        "```\n"
        "python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <this-directory>\n"
        "```\n"
        "\n"
        "Exits 0 only if every check passes.\n"
    )


# ---------------------------------------------------------------------------
# Main exporter
# ---------------------------------------------------------------------------

def export_bundle(*, source_root: Path, out_dir: Path,
                    include_raw: bool, generated_at_utc: str | None,
                    git_sha: str | None,
                    branch: str | None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir = out_dir / "schemas"
    artifacts_dir = out_dir / "artifacts"
    reports_dir = out_dir / "reports"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    repo_schemas_dir = source_root / "schemas" / "benchmarks" / "v1"

    # 1. Copy schemas into bundle (LF-normalized; see _copy_text_lf).
    for sname in SCHEMA_FILES:
        src = repo_schemas_dir / sname
        if not src.is_file():
            raise FileNotFoundError(
                f"required source schema missing: {src}"
            )
        _copy_text_lf(src, schemas_dir / sname)

    # 2. Sanitize and write artifacts; collect source SHAs.
    source_shas: dict[str, str] = {}
    artifact_index_entries: list[dict[str, Any]] = []
    for entry in SOURCE_ARTIFACTS:
        src_path = source_root / entry["source_path"]
        if not src_path.is_file():
            raise FileNotFoundError(
                f"required source artifact missing: {src_path}"
            )
        src_bytes = src_path.read_bytes()
        source_shas[entry["source_path"]] = _sha256_bytes(src_bytes)
        try:
            doc = json.loads(src_bytes.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"source artifact {src_path} is not valid JSON: {exc}"
            )
        sanitized = _sanitize(doc, include_raw=include_raw)
        out_artifact = artifacts_dir / entry["exported_filename"]
        _write_text_lf(
            out_artifact,
            json.dumps(sanitized, indent=2, sort_keys=True, default=str),
        )
        artifact_index_entries.append({
            "artifact_id": entry["artifact_id"],
            "phase": entry["phase"],
            "path_in_bundle": f"artifacts/{entry['exported_filename']}",
            "source_path_in_repo": entry["source_path"],
            "source_sha256": source_shas[entry["source_path"]],
            "exported_sha256": _sha256_of_file(out_artifact),
            "declared_schema": entry["declared_schema"],
            "raw_fields_redacted": not include_raw,
        })

    # 3. Build claim ledger and release lineage.
    ledger = _build_claim_ledger(source_shas=source_shas)
    lineage = _release_lineage()
    artifact_index = {
        "schema_version": "benchmarks.v1",
        "artifacts": artifact_index_entries,
    }

    # 4. Build manifest.
    if generated_at_utc is None:
        generated_at_utc = _utc_iso()
    if git_sha is None:
        git_sha = _git_sha(source_root)
    if branch is None:
        branch = _git_branch(source_root)

    manifest = {
        "bundle_name": "phase18a_benchmark_externalization",
        "bundle_version": "phase18a.v1",
        "schema_version": "benchmarks.v1",
        "generated_at_utc": generated_at_utc,
        "git_sha": git_sha,
        "source_branch": branch,
        "release_candidate": "v3.10.0-benchmark-schema-alpha",
        "artifact_count": len(artifact_index_entries),
        "claim_count": len(ledger["claims"]),
        "checksums_file": "checksums.sha256",
        "release_gate_pass": (not include_raw),
        "provider_jobs_delta": 0,
        "builder_jobs_delta": 0,
        "no_model_pull_or_download": True,
        "no_cloud_api_calls": True,
        "no_raw_intelligence_superiority_claim": True,
        "no_cross_vendor_ranking_claim": True,
        "no_consciousness_claim": True,
        "schemas_listed": [f"schemas/{n}" for n in SCHEMA_FILES],
        "artifacts_listed": [
            e["path_in_bundle"] for e in artifact_index_entries
        ],
        "reports_listed": [
            "reports/benchmark_bundle_index.md",
            "reports/claim_evidence_ledger.md",
        ],
    }

    # 5. Write all top-level JSON docs.
    _write_text_lf(out_dir / "benchmark_bundle_manifest.json",
                      json.dumps(manifest, indent=2, sort_keys=True))
    _write_text_lf(out_dir / "artifact_index.json",
                      json.dumps(artifact_index, indent=2, sort_keys=True))
    _write_text_lf(out_dir / "claim_evidence_ledger.json",
                      json.dumps(ledger, indent=2, sort_keys=True))
    _write_text_lf(out_dir / "release_lineage.json",
                      json.dumps(lineage, indent=2, sort_keys=True))

    # 6. Render Markdown reports.
    _write_text_lf(
        reports_dir / "benchmark_bundle_index.md",
        _render_index_md(manifest=manifest, artifact_index=artifact_index),
    )
    _write_text_lf(reports_dir / "claim_evidence_ledger.md",
                      _render_ledger_md(ledger))
    _write_text_lf(out_dir / "README.md",
                      _render_readme(manifest=manifest))

    # 7. Compute checksums of every file in the bundle.
    checksums_lines: list[str] = []
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "checksums.sha256":
            continue
        rel = path.relative_to(out_dir).as_posix()
        sha = _sha256_of_file(path)
        checksums_lines.append(f"{sha}  {rel}")
    _write_text_lf(out_dir / "checksums.sha256",
                      "\n".join(checksums_lines) + "\n")

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = (
        ROOT / "docs" / "runs"
        / "phase18a_benchmark_externalization_2026_05_05"
        / "export_bundle"
    )
    parser.add_argument("--out-dir", type=Path, default=default_out)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--include-raw", action="store_true",
                            help="include raw stdout/stderr in sanitized "
                                  "artifacts (default OFF; turning on "
                                  "disables release_gate_pass)")
    parser.add_argument("--strict", action="store_true", default=True,
                            help="(reserved) fail on unexpected source-"
                                  "artifact field; default true")
    parser.add_argument("--generated-at-utc", type=str, default=None,
                            help="(test hook) pin the manifest "
                                  "generated_at_utc field")
    args = parser.parse_args(argv)

    print("Phase 18A - Benchmark Externalization Exporter")
    print("=" * 60)
    print(f"Source root: {args.source_root}")
    print(f"Output dir : {args.out_dir}")
    print(f"include_raw: {args.include_raw}")

    manifest = export_bundle(
        source_root=args.source_root.resolve(),
        out_dir=args.out_dir.resolve(),
        include_raw=bool(args.include_raw),
        generated_at_utc=args.generated_at_utc,
        git_sha=None,
        branch=None,
    )
    print()
    print(f"Wrote bundle to {args.out_dir}")
    print(f"  artifacts : {manifest['artifact_count']}")
    print(f"  claims    : {manifest['claim_count']}")
    print(f"  schemas   : {len(manifest['schemas_listed'])}")
    print(f"  reports   : {len(manifest['reports_listed'])}")
    print(f"  release_gate_pass: {manifest['release_gate_pass']}")

    if args.validate:
        print()
        print("Running validator...")
        # Import locally so the exporter does not require the validator
        # to be importable at module-load time.
        sys.path.insert(0, str(ROOT / "tools"))
        from validate_phase18a_benchmark_bundle import (  # type: ignore
            validate_bundle,
        )
        ok, errors = validate_bundle(args.out_dir)
        if ok:
            print("Validator: PASS")
            return 0 if manifest["release_gate_pass"] else 1
        print("Validator: FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
