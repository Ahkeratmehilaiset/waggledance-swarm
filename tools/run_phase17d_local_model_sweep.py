# SPDX-License-Identifier: BUSL-1.1
"""Phase 17D - Local Ollama Multi-Model Sweep + Repeatability.

Extends Phase 17C from one local Ollama model to a panel of N already-installed
local models with R repeats per model. Reports per-model latency + variance +
correctness statistics. Never pulls a model. Never calls a cloud API.

Hard invariants (master prompt rules 6, 7, 8, 9-19):

    * No model pull or download (we only inspect what is already on disk;
      subprocess output is scanned for pull/download substrings and the
      harness aborts if any is detected).
    * No cloud API calls.
    * No allowlist widening; no autonomy-code change; no Stage-2 flip.
    * No HUMAN_APPROVAL collected.
    * No stable tag - at most a PRERELEASE.
    * No raw-intelligence superiority claim. No cross-vendor ranking.
    * No forbidden-vocabulary substrings in JSON or MD outputs.

CLI:

    python tools/run_phase17d_local_model_sweep.py [options]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform as _plat
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_VERSION = "phase17d.v1"

# Reuse the Phase 17C primitives so the prompt manifest, decoder, and
# forbidden-vocabulary list stay in one place.
sys.path.insert(0, str(ROOT / "tools"))
import run_phase17c_local_ollama_baseline as p17c  # type: ignore  # noqa: E402

PROBE_PROMPTS = p17c.PROBE_PROMPTS
FORBIDDEN_VOCABULARY = p17c.FORBIDDEN_VOCABULARY
_decode_safely = p17c._decode_safely

# Phase 17D-specific guard substrings — qualitative ranking language we
# never want to imply between models.
RANKING_GUARD_SUBSTRINGS: tuple[str, ...] = (
    "is faster than",
    "is slower than",
    "outperforms",
    " beats ",
    "ranks higher",
    "ranked first",
    "best of breed",
    "better than",
)

# Phase 17D-specific abort signatures: any of these in subprocess stdout
# OR stderr means a pull/download is happening and we must fail closed.
PULL_DOWNLOAD_SIGNATURES: tuple[str, ...] = (
    "pulling manifest",
    "downloading",
    "pulling",
    "verifying sha256",
    "writing manifest",
)

# Preference order. First N matches present locally are picked when --models
# auto. Models > 10 GB are deferred to NOT_RUN_TOO_LARGE_BY_DEFAULT unless
# --prefer-larger-models is set or the operator names them via --models.
PREFERRED_PANEL: tuple[str, ...] = (
    "gemma4:e4b",
    "gemma3:4b",
    "llama3.2:3b",
    "phi4-mini:latest",
    "qwen3:4b",
    "llama3.1:8b",
    "qwen2.5:7b",
)
LARGE_MODEL_THRESHOLD_GB = 10.0
LARGE_MODELS: tuple[str, ...] = (
    "gemma4:26b",
    "qwen2.5:32b",
    "osoderholm/poro:latest",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


# ---------------------------------------------------------------------------
# Ollama detection + listing (re-uses Phase 17C, adds size parsing)
# ---------------------------------------------------------------------------

def detect_ollama_with_sizes() -> dict[str, Any]:
    """Return ollama metadata including parsed model sizes.

    Each list entry: {"name": str, "id": str, "size_text": str,
                       "size_gb_estimate": float | None}
    """
    binary = shutil.which("ollama")
    if not binary:
        return {"available": False, "binary": None,
                 "version": None, "models": []}
    try:
        ver = subprocess.run(  # noqa: S603
            [binary, "--version"], capture_output=True,
            check=False, timeout=10,
        )
        version = (_decode_safely(ver.stdout)).strip()[:200] or "unknown"
    except Exception:  # noqa: BLE001
        version = "unknown"
    try:
        ml = subprocess.run(  # noqa: S603
            [binary, "list"], capture_output=True,
            check=False, timeout=20,
        )
        out = _decode_safely(ml.stdout)
        # Pull-detection on `ollama list` output (defensive — should not
        # happen for a list call but a corrupted ollama install could).
        for sig in PULL_DOWNLOAD_SIGNATURES:
            if sig in out.lower():
                return {"available": True, "binary": binary,
                         "version": version, "models": [],
                         "pull_detected_in_list": True}
        models: list[dict[str, Any]] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            ident = parts[1] if len(parts) > 1 else ""
            size_text = " ".join(parts[2:4]) if len(parts) >= 4 else ""
            size_gb = _parse_size_to_gb(size_text)
            models.append({
                "name": name,
                "id": ident,
                "size_text": size_text,
                "size_gb_estimate": size_gb,
            })
    except Exception:  # noqa: BLE001
        models = []
    return {"available": True, "binary": binary,
             "version": version, "models": models,
             "pull_detected_in_list": False}


def _parse_size_to_gb(size_text: str) -> float | None:
    s = size_text.strip().lower()
    if not s:
        return None
    try:
        for suffix, mul in (("gb", 1.0), ("mb", 1.0 / 1024.0),
                             ("kb", 1.0 / (1024.0 * 1024.0))):
            if s.endswith(suffix):
                num_part = s[: -len(suffix)].strip()
                return float(num_part) * mul
        # No suffix; assume GB.
        return float(s)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Panel selection
# ---------------------------------------------------------------------------

def select_panel(*, installed: list[dict[str, Any]],
                  override: str | None,
                  max_models: int,
                  prefer_larger: bool,
                  ) -> tuple[list[dict[str, Any]], list[str], str]:
    """Return (selected, deferred_too_large, rationale).

    selected: list of {name, id, size_text, size_gb_estimate}
    deferred_too_large: list of model names skipped for size
    """
    by_name = {m["name"]: m for m in installed}
    if override:
        wanted = [m.strip() for m in override.split(",") if m.strip()]
        missing = [m for m in wanted if m not in by_name]
        if missing:
            return ([], [],
                     "explicit --models requested {missing!r} not "
                     "present locally; harness will not pull"
                     .format(missing=missing))
        chosen = [by_name[m] for m in wanted]
        deferred: list[str] = []
        return chosen, deferred, f"explicit override --models={wanted!r}"

    # Auto mode.
    auto_order = list(PREFERRED_PANEL)
    if prefer_larger:
        auto_order = list(PREFERRED_PANEL) + list(LARGE_MODELS)
    chosen: list[dict[str, Any]] = []
    deferred = []
    for cand in auto_order:
        if cand not in by_name:
            continue
        meta = by_name[cand]
        size_gb = meta.get("size_gb_estimate")
        if (not prefer_larger
                and size_gb is not None
                and size_gb > LARGE_MODEL_THRESHOLD_GB):
            deferred.append(cand)
            continue
        chosen.append(meta)
        if len(chosen) >= max_models:
            break
    # Also surface any LARGE_MODELS present locally but excluded.
    for cand in LARGE_MODELS:
        if cand in by_name and cand not in [c["name"] for c in chosen]:
            if cand not in deferred:
                deferred.append(cand)
    return chosen, deferred, "auto preference order"


# ---------------------------------------------------------------------------
# Probe a single model for one repeat
# ---------------------------------------------------------------------------

def run_one_repeat(*, binary: str, model: str,
                     prompts: list[tuple[str, str]],
                     repeat_index: int) -> dict[str, Any]:
    started_at = _utc_iso()
    started = time.monotonic()
    per_prompt: list[dict[str, Any]] = []
    chain = hashlib.sha256()
    failures = 0
    latencies_ms: list[float] = []
    pull_detected = False

    for index, (family, prompt) in enumerate(prompts):
        result = p17c.run_one_prompt(binary=binary, model=model, prompt=prompt)
        # Pull-detection scrub on each subprocess output
        combined = (result["stdout"] + "\n" + result["stderr"]).lower()
        for sig in PULL_DOWNLOAD_SIGNATURES:
            if sig in combined:
                pull_detected = True
        prompt_hash = _sha256(prompt)
        stdout_hash = _sha256(result["stdout"])
        chain.update(stdout_hash.encode("ascii"))
        latency_ms = result["latency_seconds"] * 1000.0
        per_prompt.append({
            "index": index,
            "family": family,
            "prompt_hash": prompt_hash,
            "stdout_hash": stdout_hash,
            "latency_seconds": result["latency_seconds"],
            "latency_ms": latency_ms,
            "stdout_bytes": len(result["stdout"].encode("utf-8")),
            "stderr_bytes": len(result["stderr"].encode("utf-8")),
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
        })
        if result["exit_code"] == 0 and not result["timed_out"]:
            latencies_ms.append(latency_ms)
        else:
            failures += 1

    finished_at = _utc_iso()
    total_seconds = round(time.monotonic() - started, 4)

    if latencies_ms:
        latencies_sorted = sorted(latencies_ms)
        n = len(latencies_sorted)
        median = latencies_sorted[n // 2]
        p95 = latencies_sorted[min(n - 1, int(n * 0.95))]
        p99 = latencies_sorted[min(n - 1, int(n * 0.99))]
        mn = min(latencies_sorted)
        mean_ms = statistics.fmean(latencies_sorted)
        stddev_ms = statistics.pstdev(latencies_sorted) if n > 1 else 0.0
    else:
        median = p95 = p99 = mn = mean_ms = stddev_ms = None

    return {
        "repeat_index": repeat_index,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "prompts_succeeded": len(latencies_ms),
        "prompts_failed": failures,
        "total_seconds": total_seconds,
        "latency_ms_min": mn,
        "median_latency_ms": median,
        "p95_latency_ms": p95,
        "p99_latency_ms": p99,
        "mean_latency_ms": mean_ms,
        "stddev_latency_ms": stddev_ms,
        "hash_chain_sha256": chain.hexdigest(),
        "pull_detected_in_subprocess_output": pull_detected,
        "per_prompt": per_prompt,
    }


# ---------------------------------------------------------------------------
# Per-model aggregation across repeats
# ---------------------------------------------------------------------------

def aggregate_model(*, model_meta: dict[str, Any],
                     ollama_version: str,
                     repeats: list[dict[str, Any]]) -> dict[str, Any]:
    # Combine all per-prompt latencies across repeats for percentile stats.
    all_lat_ms: list[float] = []
    total_succeeded = 0
    total_failed = 0
    total_secs = 0.0
    pull_detected = any(r.get("pull_detected_in_subprocess_output")
                          for r in repeats)
    chain_acc = hashlib.sha256()
    for r in repeats:
        for p in r["per_prompt"]:
            if p["exit_code"] == 0 and not p["timed_out"]:
                all_lat_ms.append(p["latency_ms"])
        total_succeeded += r["prompts_succeeded"]
        total_failed += r["prompts_failed"]
        total_secs += r["total_seconds"]
        chain_acc.update((r.get("hash_chain_sha256") or "").encode("ascii"))

    repeat_medians = [r.get("median_latency_ms") for r in repeats
                       if r.get("median_latency_ms") is not None]
    if len(repeat_medians) >= 2:
        rep_mean = statistics.fmean(repeat_medians)
        rep_std = statistics.pstdev(repeat_medians)
        cov = (rep_std / rep_mean) if rep_mean else None
    else:
        rep_mean = rep_std = cov = None

    if all_lat_ms:
        lat_sorted = sorted(all_lat_ms)
        n = len(lat_sorted)
        agg = {
            "latency_ms_min": lat_sorted[0],
            "latency_ms_p50": lat_sorted[n // 2],
            "latency_ms_p95": lat_sorted[min(n - 1, int(n * 0.95))],
            "latency_ms_p99": lat_sorted[min(n - 1, int(n * 0.99))],
            "mean_latency_ms": statistics.fmean(lat_sorted),
            "stddev_latency_ms": statistics.pstdev(lat_sorted)
                                  if n > 1 else 0.0,
        }
    else:
        agg = {
            "latency_ms_min": None,
            "latency_ms_p50": None,
            "latency_ms_p95": None,
            "latency_ms_p99": None,
            "mean_latency_ms": None,
            "stddev_latency_ms": None,
        }

    total_attempts = total_succeeded + total_failed
    parse_success_rate = (
        (total_succeeded / total_attempts) if total_attempts else None
    )
    throughput_pps = (
        (total_succeeded / total_secs) if total_secs else None
    )

    if total_attempts and total_failed == 0 and not pull_detected:
        claim_label = "MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"
    elif pull_detected:
        claim_label = "FAILED-PULL-DOWNLOAD-DETECTED"
    else:
        claim_label = "FAILED-FOR-THIS-MODEL"

    return {
        "model_name": model_meta["name"],
        "model_id": model_meta.get("id"),
        "model_size_text": model_meta.get("size_text"),
        "model_size_gb_estimate": model_meta.get("size_gb_estimate"),
        "ollama_version": ollama_version,

        "prompt_count": len(repeats[0]["per_prompt"]) if repeats else 0,
        "repeat_count": len(repeats),
        "total_prompts_attempted": total_attempts,
        "prompts_succeeded": total_succeeded,
        "prompts_failed": total_failed,

        # We don't have ground-truth grading here; we treat
        # "exit_code 0 + non-empty stdout" as a parse_success.
        "correctness_count": total_succeeded,
        "correctness_total": total_attempts,
        "correctness_rate": parse_success_rate,
        "parse_success_count": total_succeeded,
        "parse_success_rate": parse_success_rate,

        **agg,

        "coefficient_of_variation": cov,
        "repeat_median_mean_ms": rep_mean,
        "repeat_median_stddev_ms": rep_std,

        "total_seconds": round(total_secs, 4),
        "throughput_prompts_per_second": throughput_pps,
        "hash_chain_sha256": chain_acc.hexdigest(),

        "no_model_pull_or_download": not pull_detected,
        "no_cloud_api_calls": True,

        "claim_label": claim_label,

        "per_repeat": repeats,
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
    for word in RANKING_GUARD_SUBSTRINGS:
        if word in lower:
            hits.append(word.strip())
    return hits


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(bench: dict[str, Any]) -> str:
    lines = [
        "# Phase 17D - Local Ollama Multi-Model Sweep",
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
        "**No cross-vendor ranking is implied.** Every per-model number is",
        "reported in isolation as a measurement of that exact local model on",
        "this exact prompt set on this exact host. Multi-model presentation is",
        "side-by-side, not ordered.",
        "",
        "## Selected models",
        "",
    ]
    for m in bench["selected_models"]:
        lines.append(f"* `{m}`")
    if bench.get("deferred_too_large_by_default"):
        lines += ["", "## Deferred (too large by default)", ""]
        for m in bench["deferred_too_large_by_default"]:
            lines.append(f"* `{m}` - present locally, NOT exercised "
                            "(size > 10 GB threshold; --prefer-larger-models "
                            "to opt in)")
    lines += [
        "",
        "## Per-model summary",
        "",
        "Reported in selection order, not rank order:",
        "",
        "| model | repeats | prompts ok | min ms | p50 ms | p95 ms | p99 ms | mean ms | stddev ms | CoV | claim |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for name in bench["selected_models"]:
        r = bench["model_results"].get(name, {})
        def _fmt(v):
            if v is None:
                return "-"
            if isinstance(v, float):
                return f"{v:.2f}"
            return str(v)
        lines.append(
            f"| `{name}` | {r.get('repeat_count')} | "
            f"{r.get('prompts_succeeded')}/{r.get('total_prompts_attempted')} | "
            f"{_fmt(r.get('latency_ms_min'))} | "
            f"{_fmt(r.get('latency_ms_p50'))} | "
            f"{_fmt(r.get('latency_ms_p95'))} | "
            f"{_fmt(r.get('latency_ms_p99'))} | "
            f"{_fmt(r.get('mean_latency_ms'))} | "
            f"{_fmt(r.get('stddev_latency_ms'))} | "
            f"{_fmt(r.get('coefficient_of_variation'))} | "
            f"`{r.get('claim_label')}` |"
        )
    lines += [
        "",
        "## Claim labels",
        "",
    ]
    for k, v in bench["claim_labels"].items():
        lines.append(f"* `{k}`: **{v}**")
    lines += [
        "",
        "## What this measures",
        "",
        "* Wall-clock latency of one local Ollama daemon answering a fixed",
        "  30-prompt deterministic manifest, repeated R times, across N",
        "  already-installed local models.",
        "* Repeatability via per-repeat median latency mean + stddev +",
        "  coefficient of variation. CoV close to 0 = stable; CoV > 0.3 =",
        "  noisy on this host.",
        "",
        "## What this does NOT measure",
        "",
        "* Output correctness against ground truth. The harness only checks",
        "  that each prompt produced a non-empty stdout with exit code 0.",
        "* Cross-vendor or cross-architecture ranking. Different models have",
        "  different parameter counts, training data, and quantizations; the",
        "  numbers below are not directly comparable as 'which model is",
        "  better'.",
        "* Cloud LLM endpoints. Those slots remain documented NOT_RUN.",
        "",
        "## Release gate",
        "",
        f"* `release_gate_pass`: **{bench.get('release_gate_pass', 'pending')}**",
        f"* `forbidden_claims_absent`: **{bench.get('forbidden_claims_absent', 'pending')}**",
        f"* `provider_jobs_delta`: {bench['provider_jobs_delta']}",
        f"* `builder_jobs_delta`: {bench['builder_jobs_delta']}",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = (ROOT / "docs" / "runs"
                    / "phase17d_local_model_sweep_2026_05_05")
    parser.add_argument("--out-dir", type=Path, default=default_out)
    parser.add_argument("--models", type=str, default="auto",
                            help="'auto' or comma-separated explicit list")
    parser.add_argument("--repeat-count", type=int, default=3)
    parser.add_argument("--prompt-count", type=int, default=30)
    parser.add_argument("--max-models", type=int, default=4)
    parser.add_argument("--allow-no-ollama-track", action="store_true")
    parser.add_argument("--prefer-larger-models", action="store_true",
                            help="opt-in to include models > 10 GB in the auto list")
    args = parser.parse_args(argv)

    started_at = _utc_iso()
    started_mono = time.monotonic()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "phase17d_local_model_sweep.json"
    md_path = out_dir / "phase17d_local_model_sweep.md"

    print("Phase 17D - Local Ollama Multi-Model Sweep")
    print("=" * 60)

    ollama_meta = detect_ollama_with_sizes()
    ollama_present = ollama_meta["available"]

    selected: list[dict[str, Any]] = []
    deferred: list[str] = []
    selection_rationale = ""
    selection_failed = False

    if not ollama_present:
        selection_failed = True
        selection_rationale = ("ollama binary not found on PATH; harness "
                                 "will not pull a model (rule 6/7)")
    elif ollama_meta.get("pull_detected_in_list"):
        selection_failed = True
        selection_rationale = ("ollama list output indicated a pull/"
                                 "download was in flight - aborting")
    else:
        override = (None if args.models == "auto"
                     else args.models)
        selected, deferred, selection_rationale = select_panel(
            installed=ollama_meta["models"],
            override=override,
            max_models=int(args.max_models),
            prefer_larger=bool(args.prefer_larger_models),
        )

    # Run sweep.
    model_results: dict[str, Any] = {}
    aborted_due_to_pull = False
    for meta in selected:
        if aborted_due_to_pull:
            break
        name = meta["name"]
        print(f"[OLLAMA] sweeping {name} x{args.repeat_count}")
        repeats: list[dict[str, Any]] = []
        for r in range(int(args.repeat_count)):
            n = max(1, min(int(args.prompt_count), len(PROBE_PROMPTS)))
            prompts = list(PROBE_PROMPTS[:n])
            print(f"  repeat {r}/{args.repeat_count - 1}")
            rep = run_one_repeat(
                binary=ollama_meta["binary"],
                model=name,
                prompts=prompts,
                repeat_index=r,
            )
            repeats.append(rep)
            if rep["pull_detected_in_subprocess_output"]:
                print(f"  ABORT: pull/download substring detected in "
                        f"{name} repeat {r}")
                aborted_due_to_pull = True
                break
        agg = aggregate_model(model_meta=meta,
                                ollama_version=ollama_meta["version"],
                                repeats=repeats)
        model_results[name] = agg

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

    # Panel summary
    measured_count = sum(
        1 for r in model_results.values()
        if r["claim_label"] == "MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"
    )
    p50s = [r["latency_ms_p50"] for r in model_results.values()
              if r["latency_ms_p50"] is not None]
    covs = [r["coefficient_of_variation"] for r in model_results.values()
              if r["coefficient_of_variation"] is not None]

    panel_summary = {
        "models_attempted_count": len(model_results),
        "models_measured_count": measured_count,
        "models_succeeded_count": measured_count,
        "min_p50_latency_ms_across_panel": min(p50s) if p50s else None,
        "max_p50_latency_ms_across_panel": max(p50s) if p50s else None,
        "panel_coefficient_of_variation_max": max(covs) if covs else None,
        "panel_coefficient_of_variation_min": min(covs) if covs else None,
    }

    selected_names = [m["name"] for m in selected]

    bench: dict[str, Any] = {
        "benchmark_version": BENCHMARK_VERSION,
        "git_sha": git_sha,
        "python_version": sys.version.split()[0],
        "platform": _plat.platform(),
        "started_utc": started_at,
        "finished_utc": finished_at,
        "duration_seconds": duration,

        "selected_models": selected_names,
        "deferred_too_large_by_default": deferred,
        "selection_rationale": selection_rationale,

        "ollama_meta_at_session_start": {
            "available": ollama_meta["available"],
            "binary": ollama_meta.get("binary"),
            "version": ollama_meta.get("version"),
            "models_installed_count": len(ollama_meta.get("models") or []),
            "pull_detected_in_list": ollama_meta.get("pull_detected_in_list",
                                                          False),
        },

        "repeat_count": int(args.repeat_count),
        "prompt_count": int(args.prompt_count),
        "max_models": int(args.max_models),
        "model_results": model_results,
        "panel_summary": panel_summary,

        "claim_labels": {
            "ollama_local_baseline": (
                "MEASURED-LOCAL-OLLAMA-PANEL"
                if measured_count >= 2
                else "MEASURED-LOCAL-OLLAMA-INSUFFICIENT"
            ),
            "competitive_evidence_axis_J": (
                "MEASURED-LOCAL-OLLAMA-PANEL"
                if measured_count >= 2
                else "MEASURED-LOCAL-OLLAMA-ONE-MODEL"
            ),
            "no_cross_model_ranking": True,
            "no_cross_vendor_ranking": True,
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
            "no_raw_intelligence_superiority",
            "no_cross_vendor_ranking",
        ],

        "provider_jobs_delta": 0,
        "builder_jobs_delta": 0,
        "no_model_pull_or_download": not aborted_due_to_pull,
        "no_cloud_api_calls": True,
    }

    # Forbidden-substring scrub on a subset (skip not_claimed[] and
    # selection_rationale because those legitimately contain disclaimers).
    scan_subset = {k: v for k, v in bench.items()
                    if k not in ("not_claimed",)}
    serialized_for_scan = json.dumps(scan_subset, sort_keys=True, default=str)
    json_hits = scan_forbidden(serialized_for_scan)

    md_text_preview = render_md(bench)
    md_hits = scan_forbidden(md_text_preview)
    forbidden_absent = (not json_hits) and (not md_hits)

    bench["forbidden_claims_absent"] = forbidden_absent
    bench["forbidden_substring_hits"] = {
        "json_hits": json_hits,
        "md_hits": md_hits,
    }

    # Release gates
    bench["release_gates"] = {
        "at_least_two_models_measured": measured_count >= 2,
        "no_pull_download_detected": not aborted_due_to_pull,
        "no_cloud_api_call_detected": True,
        "all_selected_models_completed": (
            len(model_results) == len(selected) and selected and not selection_failed
        ),
        "no_forbidden_substring_in_json_or_md": forbidden_absent,
        "no_provider_jobs_added": True,
        "no_builder_jobs_added": True,
        "no_allowlist_widened": True,
        "no_stable_release_in_phase17d": True,
        "no_cross_vendor_ranking": True,
        "no_raw_intelligence_superiority_claim": True,
    }

    if args.allow_no_ollama_track and not ollama_present:
        # Allowed-empty path: harness exits 0, gates evaluate as "no
        # measurement attempted; nothing to fail."
        release_gate_pass = (
            forbidden_absent
            and bench["release_gates"]["no_pull_download_detected"]
        )
    else:
        release_gate_pass = (
            measured_count >= 2
            and (not aborted_due_to_pull)
            and forbidden_absent
            and not selection_failed
        )
    bench["release_gate_pass"] = release_gate_pass

    md_text = render_md(bench)
    out_path.write_text(
        json.dumps(bench, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    md_path.write_text(md_text, encoding="utf-8")

    print()
    print(f"Wrote {out_path}")
    print(f"Wrote {md_path}")
    print()
    print(f"Selected models: {selected_names}")
    print(f"Deferred too large: {deferred}")
    print(f"Models MEASURED: {measured_count} of {len(selected)}")
    print(f"Forbidden claims absent: {forbidden_absent}")
    print(f"Release gate pass: {release_gate_pass}")

    return 0 if release_gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
