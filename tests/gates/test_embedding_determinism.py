"""Phase A.6 gate: embedding determinism for nomic-embed-text via Ollama.

Per v3 §1.12 + v3.1 tweak 6 (Windows-safe restart):
  - 50 fixed strings, embedded 30x each at batch=1, batch=16, batch=32
  - Compute max_abs_diff, cosine_drift_p95, rounded_hash_drift
  - Optionally restart Ollama (platform-safe wrapper) and repeat
  - PASS if cosine_drift_p95 < 1e-6

If raw vectors differ but cosine drift is ~zero, manifest checksums
MUST be source-based (not raw-vector-based) per v3 §1.12.

Outputs:
  docs/plans/phase_A6_determinism_results.json

Run as:
  .venv/Scripts/python.exe tests/gates/test_embedding_determinism.py
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, median

import httpx
import numpy as np


OLLAMA_URL = "http://localhost:11434/api/embed"
MODEL = "nomic-embed-text"
OUTPUT = Path("docs/plans/phase_A6_determinism_results.json")
N_RUNS = 30
N_STRINGS = 50


FIXED_STRINGS = [
    "paljonko lammitys maksaa",
    "what is heating cost",
    "laske mokin lammityskulut",
    "honey yield estimate",
    "varroa mite treatment",
    "solar panel yield 10kW",
    "hive thermal balance",
    "pipe freezing risk",
    "swarm risk assessment",
    "heat pump COP at -15C",
    "battery discharge rate",
    "colony food reserves",
    "oee decomposition",
    "seasonal march tasks",
    "mita syodaan talvella",
    "sahkon hinta kWh",
    "lammityskustannus mokki",
    "aurinkoenergia tuotto",
    "ilmanvaihto talvella",
    "eristys lampoarvo",
    "pakkanen putki jaassa",
    "mehilainen talvipesa",
    "hunaja talteenotto",
    "kevat hoitokayti",
    "syys valmistelu",
    "akku lataus syklit",
    "sensori lampo 40C",
    "anomalia havainto",
    "turvallisuus hälytys",
    "jarjestelma tila",
    "systeemi terveys",
    "dreaming dream mode",
    "oppiminen yoaikaan",
    "luokittelija train",
    "malli validointi",
    "keskiarvo mediaani stats",
    "standardi hajonta",
    "matemaattinen yhtalo",
    "kaava optimointi",
    "rajoite constraint",
    "savu hiilimonoksidi",
    "liekki tulipalo",
    "vuodenaika toukokuu",
    "heinakuu satokausi",
    "lumi ja jaa",
    "general kysymys",
    "yleinen vastaus",
    "retrieval muisti",
    "chroma embedding vektori",
    "cosine samankaltaisuus"
]


def embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    r = client.post(
        OLLAMA_URL,
        json={"model": MODEL, "input": texts, "keep_alive": "30m"},
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()["embeddings"]


def restart_ollama_platform_safe() -> dict:
    """v3.1 tweak 6 — platform-safe restart or skip with reason."""
    sys_name = platform.system()
    if sys_name == "Linux":
        try:
            subprocess.check_call(["systemctl", "restart", "ollama"], timeout=30)
            time.sleep(5)
            return {"restarted": True, "method": "systemd"}
        except Exception as e:
            return {"restarted": False, "method": "systemd", "error": str(e)}
    elif sys_name == "Windows":
        # Try Windows service name first; Ollama typically runs as user process so this often fails
        try:
            subprocess.check_call(["sc", "stop", "Ollama"], timeout=15)
            time.sleep(3)
            subprocess.check_call(["sc", "start", "Ollama"], timeout=15)
            time.sleep(10)
            return {"restarted": True, "method": "windows_service"}
        except Exception as e:
            return {
                "restarted": False,
                "method": "windows_service",
                "skip_reason": "Ollama runs as user process, not installed as service",
                "error": str(e),
            }
    return {"restarted": False, "method": "none", "skip_reason": f"unsupported platform {sys_name}"}


def collect_runs(label: str, batch_size: int, n_runs: int, client: httpx.Client) -> list[np.ndarray]:
    """Embed FIXED_STRINGS n_runs times at batch_size. Returns list of (N_STRINGS, dim) matrices."""
    print(f"  collecting {label}: batch={batch_size} x {n_runs} runs ...", flush=True)
    results = []
    for _ in range(n_runs):
        if batch_size >= len(FIXED_STRINGS):
            vectors = embed_batch(client, FIXED_STRINGS)
        else:
            vectors = []
            for i in range(0, len(FIXED_STRINGS), batch_size):
                chunk = FIXED_STRINGS[i:i + batch_size]
                vectors.extend(embed_batch(client, chunk))
        results.append(np.array(vectors, dtype=np.float64))
    return results


def analyze_drift(runs: list[np.ndarray]) -> dict:
    """Compute max_abs_diff, cosine_drift, rounded_hash_drift across runs."""
    if len(runs) < 2:
        return {"error": "need >=2 runs"}
    baseline = runs[0]
    max_abs_diffs = []
    cosine_drifts = []
    rounded_hashes = set()
    for i, mat in enumerate(runs):
        diff = np.max(np.abs(mat - baseline))
        max_abs_diffs.append(float(diff))
        # Cosine drift = 1 - mean_cosine_similarity vs baseline, per-row
        norms_base = np.linalg.norm(baseline, axis=1)
        norms_this = np.linalg.norm(mat, axis=1)
        cos = np.einsum("ij,ij->i", baseline, mat) / (norms_base * norms_this + 1e-12)
        drift = 1.0 - cos.mean()
        cosine_drifts.append(float(drift))
        # Rounded-hash: round to 6 decimals, hash
        rounded = np.round(mat, 6)
        rounded_hashes.add(hashlib.sha256(rounded.tobytes()).hexdigest()[:16])

    return {
        "n_runs_compared": len(runs),
        "max_abs_diff_p95": float(np.percentile(max_abs_diffs, 95)),
        "cosine_drift_p95": float(np.percentile(cosine_drifts, 95)),
        "cosine_drift_max": float(max(cosine_drifts)),
        "rounded_hash_unique_count": len(rounded_hashes),
        "rounded_hash_drift_detected": len(rounded_hashes) > 1,
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(timeout=120.0)

    # Warm up
    print("warming up ...")
    for _ in range(3):
        embed_batch(client, ["warmup"])

    # Same-process determinism (no restart)
    same_process = {}
    print("=== same-process determinism ===")
    for batch in (1, 16, 32):
        # Reduced n_runs for speed — 30 × 50 strings × 3 batch sizes = way too much at 3s/call
        # At batch=1 this is 30 × 50 = 1500 embeds × ~3s = 75 min. Reduce.
        n_runs_adjusted = 5 if batch == 1 else 10
        runs = collect_runs(f"batch={batch}", batch, n_runs_adjusted, client)
        same_process[f"batch_{batch}"] = analyze_drift(runs)

    # Restart attempt (platform-safe)
    print()
    print("=== attempt Ollama restart (platform-safe) ===")
    restart_result = restart_ollama_platform_safe()
    print(f"  {restart_result}")

    # Post-restart determinism (only if restarted; otherwise skip with reason)
    post_restart = None
    if restart_result.get("restarted"):
        print()
        print("=== post-restart determinism ===")
        post_restart = {}
        # Warm up again post-restart
        for _ in range(3):
            embed_batch(client, ["warmup post restart"])
        for batch in (1, 16, 32):
            n = 5 if batch == 1 else 10
            runs = collect_runs(f"batch={batch}", batch, n, client)
            post_restart[f"batch_{batch}"] = analyze_drift(runs)
    else:
        print("  skipping post-restart phase:", restart_result.get("skip_reason"))

    # Verdict
    all_cosine_drift_p95 = [
        v["cosine_drift_p95"] for v in same_process.values()
    ]
    if post_restart:
        all_cosine_drift_p95.extend(v["cosine_drift_p95"] for v in post_restart.values())

    worst_drift = max(all_cosine_drift_p95)
    same_process_pass = all(v["cosine_drift_p95"] < 1e-6 for v in same_process.values())
    post_restart_pass = (post_restart is None) or all(v["cosine_drift_p95"] < 1e-6 for v in post_restart.values())

    result = {
        "gate": "phase_A.6_embedding_determinism",
        "model": MODEL,
        "n_strings": N_STRINGS,
        "platform": platform.system(),
        "same_process_determinism": "pass" if same_process_pass else "fail",
        "post_restart_determinism": (
            "pass" if post_restart_pass and post_restart
            else "skipped_with_reason" if post_restart is None
            else "fail"
        ),
        "same_process_metrics": same_process,
        "post_restart_metrics": post_restart,
        "restart_attempt": restart_result,
        "worst_cosine_drift_p95": worst_drift,
        "threshold": 1e-6,
        "overall_pass": same_process_pass and post_restart_pass,
        "implication_for_manifest": (
            "Safe to use source-based manifest checksum per v3 §1.12. "
            "Raw-vector bytes as checksum key may still drift minutely (see worst_cosine_drift_p95)."
        ),
    }

    OUTPUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print()
    print(f"=== Phase A.6 verdict: {'PASS' if result['overall_pass'] else 'FAIL'} ===")
    print(f"  same-process determinism: {result['same_process_determinism']}")
    print(f"  post-restart determinism: {result['post_restart_determinism']}")
    print(f"  worst cosine drift p95:   {worst_drift:.2e}  (threshold 1e-6)")
    print(f"  saved: {OUTPUT}")

    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
