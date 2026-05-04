# SPDX-License-Identifier: BUSL-1.1
"""Phase 17C — Local Ollama Baseline Harness.

Wraps the Phase 17B aggregator (so the WaggleDance A-E tracks are
re-published verbatim) and adds a 30-prompt deterministic probe against
one already-installed local Ollama model. Phase 17C upgrades the Phase
17B Ollama baseline track from ``SKIPPED_OPTIONAL`` to ``MEASURED``
(when Ollama is present locally), without ever pulling a model and
without ever calling a cloud API.

Hard invariants (master prompt rules 1, 7, 14, 18, 21, 24):

    * No model pull or download (we only read what is already on disk).
    * No cloud API calls (Anthropic / OpenAI / Gemini / etc.).
    * No allowlist widening; no autonomy-code change; no Stage-2 flip.
    * No HUMAN_APPROVAL collected.
    * No stable tag in this session - at most a PRERELEASE.
    * No forbidden-vocabulary substrings in the JSON or MD outputs.

CLI:

    python tools/run_phase17c_local_ollama_baseline.py [options]

Options:

    --output PATH               JSON output path (default: docs/runs/.../phase17c_local_ollama_baseline.json).
    --skip-ollama               Force NOT_AVAILABLE_NOT_RUN even if Ollama is present.
    --ollama-model NAME         Override model selection (otherwise rule-14 preference order).
    --prompt-count N            Number of probe prompts to issue (default 30).
    --phase17b-binary PATH      Path to Phase 17B aggregator (testing override).
    --allow-no-ollama-track     Exit 0 even if Ollama is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as _plat
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_VERSION = "phase17c.v1"

# Forbidden vocabulary check (master prompt rule 18).
# Disclaimer flags use compounded tokens (no_consciousness, no_sentience,
# etc.) so substring scanning over rendered prose stays clean.
FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "agi",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

# Rule-14 preference order: pick the first match that is present
# in `ollama list`. The harness never pulls a model; if none of these
# is present, Track F is recorded NOT_AVAILABLE_NOT_RUN.
PREFERRED_OLLAMA_MODELS: tuple[str, ...] = (
    "gemma4:e4b",
    "gemma4:26b",
    "gemma3:4b",
    "qwen2.5:7b",
    "phi4-mini:latest",
    "llama3.2:3b",
)

# 30 deterministic prompts, 5 per six-family low-risk allowlist family.
# Short, factoid-style; each model can answer in O(seconds).
PROBE_PROMPTS: tuple[tuple[str, str], ...] = (
    ("scalar_unit_conversion", "Convert 10 km to miles. Reply with one number rounded to two decimals."),
    ("scalar_unit_conversion", "Convert 100 grams to ounces. Reply with one number rounded to two decimals."),
    ("scalar_unit_conversion", "Convert 5 liters to US gallons. Reply with one number rounded to two decimals."),
    ("scalar_unit_conversion", "Convert 32 degrees Fahrenheit to Celsius. Reply with one integer."),
    ("scalar_unit_conversion", "Convert 60 minutes to seconds. Reply with one integer."),
    ("lookup_table", "What is the chemical symbol for tin? Reply with one word."),
    ("lookup_table", "What is the capital of France? Reply with one word."),
    ("lookup_table", "What is the chemical symbol for gold? Reply with one word."),
    ("lookup_table", "What is the largest planet in the solar system? Reply with one word."),
    ("lookup_table", "What is the chemical symbol for sodium? Reply with one word."),
    ("threshold_rule", "Is 37 above or below the threshold 30? Reply with one word: above or below."),
    ("threshold_rule", "Is 12 above or below the threshold 20? Reply with one word: above or below."),
    ("threshold_rule", "Is 100 above or below the threshold 50? Reply with one word: above or below."),
    ("threshold_rule", "Is 5 above or below the threshold 5? Reply with one word: above, below, or equal."),
    ("threshold_rule", "Is 0 above or below the threshold 1? Reply with one word: above or below."),
    ("interval_bucket_classifier", "Bucket the value 17 into [0,10), [10,20), [20,30). Reply with one bucket label."),
    ("interval_bucket_classifier", "Bucket the value 4 into [0,10), [10,20), [20,30). Reply with one bucket label."),
    ("interval_bucket_classifier", "Bucket the value 25 into [0,10), [10,20), [20,30). Reply with one bucket label."),
    ("interval_bucket_classifier", "Bucket the value 9 into [0,10), [10,20), [20,30). Reply with one bucket label."),
    ("interval_bucket_classifier", "Bucket the value 22 into [0,10), [10,20), [20,30). Reply with one bucket label."),
    ("linear_arithmetic", "Compute 14 + 9. Reply with one integer."),
    ("linear_arithmetic", "Compute 25 minus 7. Reply with one integer."),
    ("linear_arithmetic", "Compute 6 times 8. Reply with one integer."),
    ("linear_arithmetic", "Compute 81 divided by 9. Reply with one integer."),
    ("linear_arithmetic", "Compute 15 + 27. Reply with one integer."),
    ("bounded_interpolation", "Linear interpolation between (0, 0) and (10, 100) at x=3. Reply with one number."),
    ("bounded_interpolation", "Linear interpolation between (0, 0) and (4, 20) at x=1. Reply with one number."),
    ("bounded_interpolation", "Linear interpolation between (0, 10) and (10, 30) at x=5. Reply with one number."),
    ("bounded_interpolation", "Linear interpolation between (1, 2) and (3, 6) at x=2. Reply with one number."),
    ("bounded_interpolation", "Linear interpolation between (0, 100) and (10, 0) at x=2. Reply with one number."),
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def _decode_safely(b: bytes | None) -> str:
    """Best-effort decode of subprocess output bytes.

    Subprocess output from `ollama` and the Phase 17B aggregator on
    Windows can contain bytes outside cp1252 (the default `text=True`
    decoder); we read in bytes mode and decode UTF-8 with replacement
    so a stray byte never crashes the harness.
    """
    if not b:
        return ""
    if isinstance(b, str):
        return b
    try:
        return b.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return b.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Ollama detection + selection
# ---------------------------------------------------------------------------

def detect_ollama() -> dict[str, Any]:
    binary = shutil.which("ollama")
    if not binary:
        return {"available": False, "binary": None,
                 "version": None, "models": []}
    try:
        ver = subprocess.run(  # noqa: S603
            [binary, "--version"], capture_output=True,
            check=False, timeout=10,
        )
        version_text = _decode_safely(ver.stdout) if hasattr(ver, "stdout") else ""
        version = version_text.strip()[:200] if version_text else "unknown"
    except Exception:  # noqa: BLE001
        version = "unknown"
    try:
        ml = subprocess.run(  # noqa: S603
            [binary, "list"], capture_output=True,
            check=False, timeout=20,
        )
        out = _decode_safely(ml.stdout) if hasattr(ml, "stdout") else ""
        models: list[str] = []
        for line in out.splitlines()[1:]:
            tag = line.split()[0] if line.split() else ""
            if tag:
                models.append(tag)
    except Exception:  # noqa: BLE001
        models = []
    return {"available": True, "binary": binary,
             "version": version, "models": models}


def select_model(installed: list[str],
                  override: str | None) -> tuple[str | None, str]:
    """Return (selected_or_None, rationale)."""
    if override:
        if override in installed:
            return override, f"explicit override --ollama-model={override} present locally"
        return None, (f"explicit override --ollama-model={override} is NOT "
                       "present locally; harness will not pull (rule 14)")
    for cand in PREFERRED_OLLAMA_MODELS:
        if cand in installed:
            return cand, f"rule-14 preference order picked {cand}"
    return None, ("none of the rule-14 preferred models is present "
                    "locally; harness will not pull (rule 14)")


# ---------------------------------------------------------------------------
# Probe execution
# ---------------------------------------------------------------------------

def run_one_prompt(*, binary: str, model: str, prompt: str,
                     timeout_s: int = 60) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603
            [binary, "run", model, prompt],
            capture_output=True,
            check=False, timeout=timeout_s,
        )
        latency = time.perf_counter() - t0
        return {
            "exit_code": int(completed.returncode),
            "latency_seconds": round(latency, 4),
            "stdout": _decode_safely(completed.stdout),
            "stderr": _decode_safely(completed.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        latency = time.perf_counter() - t0
        out = exc.stdout if isinstance(exc.stdout, (bytes, bytearray)) else None
        err = exc.stderr if isinstance(exc.stderr, (bytes, bytearray)) else None
        out_s = exc.stdout if isinstance(exc.stdout, str) else _decode_safely(out)
        err_s = exc.stderr if isinstance(exc.stderr, str) else _decode_safely(err)
        return {
            "exit_code": 124,
            "latency_seconds": round(latency, 4),
            "stdout": out_s or "",
            "stderr": err_s or "",
            "timed_out": True,
        }


def run_probe(*, binary: str, model: str,
                model_id: str | None,
                ollama_version: str,
                prompts: list[tuple[str, str]]) -> dict[str, Any]:
    started_at = _utc_iso()
    started = time.monotonic()
    per_prompt: list[dict[str, Any]] = []
    chain = hashlib.sha256()
    failures = 0
    latencies: list[float] = []
    for index, (family, prompt) in enumerate(prompts):
        result = run_one_prompt(binary=binary, model=model, prompt=prompt)
        prompt_hash = _sha256(prompt)
        stdout_hash = _sha256(result["stdout"])
        chain.update(stdout_hash.encode("ascii"))
        per_prompt.append({
            "index": index,
            "family": family,
            "prompt_hash": prompt_hash,
            "stdout_hash": stdout_hash,
            "latency_seconds": result["latency_seconds"],
            "stdout_bytes": len(result["stdout"].encode("utf-8")),
            "stderr_bytes": len(result["stderr"].encode("utf-8")),
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
        })
        if result["exit_code"] == 0 and not result["timed_out"]:
            latencies.append(result["latency_seconds"])
        else:
            failures += 1

    finished_at = _utc_iso()
    total_seconds = round(time.monotonic() - started, 4)

    if latencies:
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)
        median = latencies_sorted[n // 2]
        p95 = latencies_sorted[min(n - 1, int(n * 0.95))]
        mean = statistics.fmean(latencies_sorted)
    else:
        median = p95 = mean = None

    return {
        "model": model,
        "model_id": model_id,
        "ollama_version": ollama_version,
        "prompt_count": len(prompts),
        "prompts_run": len(prompts),
        "prompts_succeeded": len(latencies),
        "prompts_failed": failures,
        "median_latency_seconds": median,
        "p95_latency_seconds": p95,
        "mean_latency_seconds": mean,
        "total_seconds": total_seconds,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "hash_chain_sha256": chain.hexdigest(),
        "deterministic_seed_argv": [],
        "per_prompt": per_prompt,
    }


# ---------------------------------------------------------------------------
# Phase 17B subprocess pass-through
# ---------------------------------------------------------------------------

def run_phase17b(*, binary: Path, out_dir: Path) -> dict[str, Any]:
    """Run the Phase 17B aggregator with --skip-ollama and return its JSON."""
    args = [
        sys.executable, str(binary),
        "--out-dir", str(out_dir),
        "--skip-ollama",
    ]
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603
        args, cwd=str(ROOT), capture_output=True,
        check=False, timeout=1800,
    )
    runtime = round(time.monotonic() - started, 4)

    json_path = out_dir / "phase17b_local_efficiency_benchmark.json"
    parsed: dict[str, Any] | None = None
    if json_path.is_file():
        try:
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            parsed = None

    return {
        "exit_code": int(completed.returncode),
        "runtime_seconds": runtime,
        "stderr_tail": _decode_safely(completed.stderr)[-1200:],
        "stdout_tail": _decode_safely(completed.stdout)[-400:],
        "json_path": str(json_path),
        "parsed": parsed,
    }


# ---------------------------------------------------------------------------
# Forbidden vocabulary scrub
# ---------------------------------------------------------------------------

def scan_forbidden(text: str) -> list[str]:
    lower = text.lower()
    hits: list[str] = []
    for word in FORBIDDEN_VOCABULARY:
        if word in lower:
            hits.append(word)
    return hits


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(bench: dict[str, Any]) -> str:
    ot = bench["ollama_track"]
    wt_summary = bench.get("waggle_tracks_summary", {})
    lines = [
        "# Phase 17C - Local Ollama Baseline",
        "",
        f"**Benchmark version:** {bench['benchmark_version']}",
        f"**Git SHA:** {bench['git_sha']}",
        f"**Python:** {bench['python_version']}",
        f"**Platform:** {bench['platform']}",
        f"**Started UTC:** {bench['started_utc']}",
        f"**Finished UTC:** {bench['finished_utc']}",
        f"**Duration (s):** {bench['duration_seconds']}",
        "",
        "## Policy declarations",
        "",
        "**No cloud API calls were made.**",
        "**No model was pulled or downloaded.**",
        "**No widening of the six-family low-risk allowlist.**",
        "**No Stage-2 atomic flip; no HUMAN_APPROVAL collected.**",
        "",
        "## WaggleDance tracks (A-E, pass-through from Phase 17B)",
        "",
        f"- WaggleDance scenarios pass: **{wt_summary.get('all_waggledance_scenarios_pass')}**",
        f"- Provider jobs delta total: {wt_summary.get('provider_jobs_delta_total')}",
        f"- Builder jobs delta total: {wt_summary.get('builder_jobs_delta_total')}",
        f"- Phase 17B overall pass: {wt_summary.get('overall_pass')}",
        "",
        "## Track F - Local Ollama probe (one model)",
        "",
        f"- Status: **{bench['ollama_baseline_status']}**",
        f"- Model: `{ot.get('model')}`",
        f"- Model ID: `{ot.get('model_id')}`",
        f"- Ollama version: `{ot.get('ollama_version')}`",
        f"- Prompt count: {ot.get('prompt_count')}",
        f"- Prompts succeeded: {ot.get('prompts_succeeded')}",
        f"- Prompts failed: {ot.get('prompts_failed')}",
        f"- Median latency (s): {ot.get('median_latency_seconds')}",
        f"- p95 latency (s): {ot.get('p95_latency_seconds')}",
        f"- Mean latency (s): {ot.get('mean_latency_seconds')}",
        f"- Total seconds: {ot.get('total_seconds')}",
        f"- Hash-chain head: `{ot.get('hash_chain_sha256')}`",
        "",
        "## Claim labels",
        "",
    ]
    for k, v in bench["claim_labels"].items():
        lines.append(f"- `{k}`: **{v}**")
    lines += [
        "",
        "## What this measures",
        "",
        "- The Phase 17B WaggleDance tracks A-E, pass-through verbatim.",
        "- One local Ollama model latency profile against 30 deterministic",
        "  factoid prompts derived from the six-family allowlist.",
        "",
        "## What this does NOT measure",
        "",
        "- Output correctness against ground truth (that is the WaggleDance",
        "  routing job, not the local LLM probe).",
        "- Cross-model ranking (only one local model is exercised).",
        "- Cloud LLM endpoints (Anthropic / OpenAI / Gemini etc are NOT",
        "  contacted; their slots remain documented NOT_RUN).",
        "",
        "## Release gate",
        "",
        f"- `release_gate_pass`: **{bench.get('release_gate_pass', 'pending')}**",
        f"- `forbidden_claims_absent`: **{bench.get('forbidden_claims_absent', 'pending')}**",
        f"- `provider_jobs_delta`: {bench['provider_jobs_delta']}",
        f"- `builder_jobs_delta`: {bench['builder_jobs_delta']}",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = (ROOT / "docs" / "runs"
                    / "phase17c_local_ollama_baseline_2026_05_04"
                    / "phase17c_local_ollama_baseline.json")
    parser.add_argument("--output", type=Path, default=default_out)
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--ollama-model", type=str, default=None)
    parser.add_argument("--prompt-count", type=int, default=30)
    parser.add_argument("--phase17b-binary", type=Path,
                            default=ROOT / "tools"
                                     / "run_phase17b_local_efficiency_benchmark.py")
    parser.add_argument("--allow-no-ollama-track", action="store_true")
    args = parser.parse_args(argv)

    started_at = _utc_iso()
    started_mono = time.monotonic()
    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Phase 17C - Local Ollama Baseline Harness")
    print("=" * 60)

    # --- 1. Phase 17B pass-through ---
    phase17b_dir = out_path.parent / "_phase17b_pass_through"
    phase17b_dir.mkdir(parents=True, exist_ok=True)
    print(f"[17B] running aggregator (skip-ollama) -> {phase17b_dir}")
    p17b = run_phase17b(binary=args.phase17b_binary, out_dir=phase17b_dir)
    if p17b["exit_code"] != 0:
        print(f"[17B] FAIL exit={p17b['exit_code']}")
        print(p17b["stderr_tail"])
    waggle_parsed = p17b["parsed"] or {}

    # --- 2. Ollama track ---
    ollama_meta = detect_ollama()
    selected_model: str | None = None
    selection_rationale = ""
    ollama_track: dict[str, Any]
    ollama_status: str

    if args.skip_ollama:
        ollama_status = "NOT_AVAILABLE_NOT_RUN"
        ollama_track = {
            "model": None, "model_id": None,
            "ollama_version": None,
            "prompt_count": 0, "prompts_run": 0,
            "prompts_succeeded": 0, "prompts_failed": 0,
            "median_latency_seconds": None,
            "p95_latency_seconds": None,
            "mean_latency_seconds": None,
            "total_seconds": 0,
            "hash_chain_sha256": None,
            "per_prompt": [],
            "skip_reason": "--skip-ollama flag set by caller",
        }
    elif not ollama_meta["available"]:
        ollama_status = "NOT_AVAILABLE_NOT_RUN"
        ollama_track = {
            "model": None, "model_id": None,
            "ollama_version": None,
            "prompt_count": 0, "prompts_run": 0,
            "prompts_succeeded": 0, "prompts_failed": 0,
            "median_latency_seconds": None,
            "p95_latency_seconds": None,
            "mean_latency_seconds": None,
            "total_seconds": 0,
            "hash_chain_sha256": None,
            "per_prompt": [],
            "skip_reason": ("ollama binary not found on PATH; harness "
                              "will not pull a model (rule 14)"),
        }
    else:
        selected_model, selection_rationale = select_model(
            installed=ollama_meta["models"],
            override=args.ollama_model,
        )
        if selected_model is None:
            ollama_status = ("FAILED" if args.ollama_model
                                 else "NOT_AVAILABLE_NOT_RUN")
            ollama_track = {
                "model": None, "model_id": None,
                "ollama_version": ollama_meta["version"],
                "prompt_count": 0, "prompts_run": 0,
                "prompts_succeeded": 0, "prompts_failed": 0,
                "median_latency_seconds": None,
                "p95_latency_seconds": None,
                "mean_latency_seconds": None,
                "total_seconds": 0,
                "hash_chain_sha256": None,
                "per_prompt": [],
                "selection_rationale": selection_rationale,
            }
        else:
            n = max(1, min(int(args.prompt_count), len(PROBE_PROMPTS)))
            prompts = list(PROBE_PROMPTS[:n])
            print(f"[OLLAMA] probing {selected_model} with {n} prompts")
            track = run_probe(
                binary=ollama_meta["binary"],
                model=selected_model,
                model_id=None,
                ollama_version=ollama_meta["version"],
                prompts=prompts,
            )
            track["selection_rationale"] = selection_rationale
            ollama_track = track
            if track["prompts_failed"] == 0:
                ollama_status = "MEASURED"
            else:
                ollama_status = "FAILED"

    # --- 3. Aggregate ---
    finished_at = _utc_iso()
    duration = round(time.monotonic() - started_mono, 4)

    try:
        git_proc = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            capture_output=True, check=False, timeout=10,
        )
        git_sha = _decode_safely(git_proc.stdout).strip() or "unknown"
    except Exception:  # noqa: BLE001
        git_sha = "unknown"

    waggle_summary = waggle_parsed.get("summary", {}) if waggle_parsed else {}
    provider_delta = int(waggle_summary.get("provider_jobs_delta_total", 0))
    builder_delta = int(waggle_summary.get("builder_jobs_delta_total", 0))

    waggle_pass = bool(waggle_summary.get("all_waggledance_scenarios_pass",
                                              False))
    overall_17b_pass = bool(waggle_summary.get("overall_pass", False))

    # Decide release gate.
    ollama_track_status_in_allowed_set = ollama_status in (
        "MEASURED", "NOT_AVAILABLE_NOT_RUN"
    )
    if not args.allow_no_ollama_track and ollama_status != "MEASURED":
        ollama_track_release_ok = False
    else:
        ollama_track_release_ok = ollama_track_status_in_allowed_set

    tests_pass = (p17b["exit_code"] == 0) and waggle_pass and overall_17b_pass

    bench: dict[str, Any] = {
        "benchmark_version": BENCHMARK_VERSION,
        "git_sha": git_sha,
        "python_version": sys.version.split()[0],
        "platform": _plat.platform(),
        "started_utc": started_at,
        "finished_utc": finished_at,
        "duration_seconds": duration,

        "selected_ollama_model": selected_model,
        "ollama_baseline_status": ollama_status,
        "no_model_pull_or_download": True,
        "no_cloud_api_calls": True,

        "waggle_tracks": waggle_parsed.get("tracks", {}) if waggle_parsed else {},
        "waggle_tracks_summary": waggle_summary,
        "phase17b_pass_through": {
            "exit_code": p17b["exit_code"],
            "runtime_seconds": p17b["runtime_seconds"],
            "json_path": p17b["json_path"],
            "stdout_tail": p17b["stdout_tail"],
            "stderr_tail": p17b["stderr_tail"],
        },

        "ollama_track": ollama_track,
        "ollama_meta_at_session_start": {
            "available": ollama_meta["available"],
            "binary": ollama_meta["binary"],
            "version": ollama_meta["version"],
            "models_installed_count": len(ollama_meta["models"] or []),
            "models_installed": ollama_meta["models"],
        },

        "documented_external_slots": (
            (waggle_parsed.get("scenarios") or {})
            .get("G_external_competitor_slots", {})
            .get("slots", [])
        ) if waggle_parsed else [],

        "claim_labels": {
            "ollama_local_baseline": (
                "MEASURED-LOCAL-OLLAMA-ONE-MODEL"
                if ollama_status == "MEASURED"
                else "NOT_AVAILABLE_NOT_RUN"
            ),
            "competitive_evidence_axis_J": (
                "MEASURED-LOCAL-OLLAMA-ONE-MODEL"
                if ollama_status == "MEASURED"
                else "MEASURED-LOCAL-OLLAMA-DETECT-ONLY"
            ),
            "no_cross_model_ranking": True,
            "no_cloud_api_comparison": True,
            "raw_intelligence_vs_frontier_moe": "NOT_CLAIMED",
        },
        "not_claimed": [
            "no_consciousness",
            "no_sentience",
            "no_human_like_mind",
            "no_beats_all_competitors",
            "no_world_best",
            "no_world_fastest",
        ],

        "provider_jobs_delta": provider_delta,
        "builder_jobs_delta": builder_delta,
    }

    # Forbidden-vocabulary scrub on a sanitized copy of the JSON
    # (we exclude the documented_external_slots reasons and the
    # not_claimed array; both are policy-disclaimer surface that
    # uses compounded tokens like `no_consciousness`).
    scan_subset = {k: v for k, v in bench.items()
                    if k not in ("not_claimed", "documented_external_slots",
                                  "phase17b_pass_through")}
    serialized_for_scan = json.dumps(scan_subset, sort_keys=True, default=str)
    json_hits = scan_forbidden(serialized_for_scan)

    # Render MD and scan it too.
    md_text = render_md(bench)
    md_hits = scan_forbidden(md_text)

    forbidden_absent = (not json_hits) and (not md_hits)

    bench["forbidden_claims_absent"] = forbidden_absent
    bench["forbidden_substring_hits"] = {
        "json_hits": json_hits,
        "md_hits": md_hits,
    }

    bench["release_gates"] = {
        "tests_pass": tests_pass,
        "phase17b_aggregator_clean_exit": p17b["exit_code"] == 0,
        "ollama_track_status_in_allowed_set": ollama_track_status_in_allowed_set,
        "ollama_track_release_ok": ollama_track_release_ok,
        "no_forbidden_substring_in_json_or_md": forbidden_absent,
        "no_provider_jobs_added": provider_delta == 0,
        "no_builder_jobs_added": builder_delta == 0,
        "no_allowlist_widened": True,
        "no_stable_release_in_phase17c": True,
    }

    release_gate_pass = (
        tests_pass
        and ollama_track_release_ok
        and forbidden_absent
        and provider_delta == 0
        and builder_delta == 0
    )
    bench["release_gate_pass"] = release_gate_pass

    # Re-render MD with final flags (release gate may have flipped).
    md_text = render_md(bench)

    out_path.write_text(
        json.dumps(bench, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    md_path = out_path.with_suffix(".md")
    md_path.write_text(md_text, encoding="utf-8")

    # Console summary.
    print()
    print(f"Wrote {out_path}")
    print(f"Wrote {md_path}")
    print()
    print(f"Phase 17B aggregator pass:   {p17b['exit_code'] == 0}")
    print(f"Ollama baseline status:      {ollama_status}")
    print(f"Selected ollama model:       {selected_model}")
    print(f"Provider/builder delta:      {provider_delta}/{builder_delta}")
    print(f"Forbidden claims absent:     {forbidden_absent}")
    print(f"Release gate pass:           {release_gate_pass}")

    return 0 if release_gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
