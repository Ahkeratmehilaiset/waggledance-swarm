"""R21.1 — oracle-backed A/B proof for HexTopologyRegistry.select_origin_cell.

Per `iterations/codex_scout_tasks/r21_synthesis_2026_05_10.md` and
`iterations/codex_scout_tasks/r21_operator_decisions_2026_05_10.md`:

- corpus: `tests/oracle/*.yaml` (15 files × ~30 utterances each =
  ~450 labelled positive/negative routing pairs)
- call site: `HexTopologyRegistry.select_origin_cell` (post-#171
  heuristic)
- A/B harness: `waggledance.core.bridge_llm.ABHarness` from R20.3
- quality metric: symmetric macro-average across oracle files
    quality_arm = (
        correct_positive_routings / total_positive_utterances
        + correct_negative_rejections / total_negative_utterances
    ) / 2
- treatment_share = 1.0 for the bench so BOTH arms are computed on
  EVERY utterance (the runtime A/B pattern with share=0.5 routes
  per-call; for the bench we want full sweeps on both arms so the
  per-arm quality is statistically meaningful)
- result recorded in `EVOLUTION_INDEX.md` regardless of >=20%
  threshold (Decision B + R20 rule 17: log honestly, keep
  treatment disabled if below threshold)

Run:

    python tools/run_r21_oracle_ab_proof.py \
        --out-json iterations/codex_scout_tasks/r21_oracle_ab_proof.json

Profile S compatibility: when `BridgeLLMClient.default().is_enabled()`
is False (Profile S, WAGGLE_BRIDGE_LLM_ENABLED=0), treatment falls
through to heuristic so delta_quality = 0. The result must record
local_llm_status so downstream readers can distinguish "LLM
unavailable" from "LLM measurably worse".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORACLE_DIR = REPO_ROOT / "tests" / "oracle"
DEFAULT_HEX_CONFIG = REPO_ROOT / "configs" / "hex_cells.yaml"

# Make `waggledance` importable when run as `python tools/...`
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_oracle_corpus(oracle_dir: Path) -> list[dict[str, Any]]:
    """Load all tests/oracle/*.yaml files except those starting with `_`
    (e.g. `_off_domain.yaml`, which is a special non-routable marker)."""
    out: list[dict[str, Any]] = []
    for yaml_path in sorted(oracle_dir.glob("*.yaml")):
        if yaml_path.name.startswith("_"):
            continue
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        if not data.get("cell"):
            continue
        out.append({
            "file": yaml_path.name,
            "solver": data.get("solver", ""),
            "domain": data.get("domain", ""),
            "cell": data.get("cell", ""),
            "positive": list(data.get("positive") or []),
            "negative": list(data.get("negative") or []),
        })
    return out


def quality_arm(
    oracles: list[dict[str, Any]],
    route_fn: Callable[[str], str | None],
) -> dict[str, Any]:
    """Compute symmetric macro-average quality for a routing function.

    Returns a dict:
      quality          — macro-average across oracle files
      per_file         — list of {file, cell, pos_score, neg_score, file_score}
      micro_pos_correct, micro_pos_total
      micro_neg_correct, micro_neg_total

    The micro counters are diagnostics per the operator-decisions doc
    "should still report supporting micro-average numbers as
    secondary diagnostics when cheap".
    """
    per_file: list[dict[str, Any]] = []
    micro_pos_correct = 0
    micro_pos_total = 0
    micro_neg_correct = 0
    micro_neg_total = 0
    for oracle in oracles:
        expected_cell = oracle["cell"]
        positives = oracle["positive"]
        negatives = oracle["negative"]
        pos_correct = sum(1 for u in positives if route_fn(u) == expected_cell)
        neg_correct = sum(1 for u in negatives if route_fn(u) != expected_cell)
        pos_score = pos_correct / len(positives) if positives else 0.0
        neg_score = neg_correct / len(negatives) if negatives else 0.0
        file_score = (pos_score + neg_score) / 2
        per_file.append({
            "file": oracle["file"],
            "cell": expected_cell,
            "pos_total": len(positives),
            "pos_correct": pos_correct,
            "pos_score": round(pos_score, 4),
            "neg_total": len(negatives),
            "neg_correct": neg_correct,
            "neg_score": round(neg_score, 4),
            "file_score": round(file_score, 4),
        })
        micro_pos_correct += pos_correct
        micro_pos_total += len(positives)
        micro_neg_correct += neg_correct
        micro_neg_total += len(negatives)
    macro_quality = (
        sum(f["file_score"] for f in per_file) / len(per_file)
        if per_file else 0.0
    )
    return {
        "quality": round(macro_quality, 4),
        "per_file": per_file,
        "micro_pos_correct": micro_pos_correct,
        "micro_pos_total": micro_pos_total,
        "micro_neg_correct": micro_neg_correct,
        "micro_neg_total": micro_neg_total,
    }


def parse_cell_from_llm_text(text: str, valid_cells: set[str]) -> str | None:
    """Find the first valid cell name occurring in the LLM response.

    Used to parse treatment-arm output back into a routing decision.
    Case-insensitive substring match — keep it simple in v1; if
    quality suffers from ambiguous parses, R21.1 follow-up can swap
    in a stricter parser.
    """
    if not text:
        return None
    text_lower = text.lower()
    for cell in valid_cells:
        if cell.lower() in text_lower:
            return cell
    return None


def build_treatment_prompt(query: str, valid_cells: list[str]) -> str:
    """Build the prompt the treatment arm sends to BridgeLLMClient."""
    return (
        f"You are a routing decision maker for the WaggleDance hex topology. "
        f"Route this query to exactly ONE of the available cells.\n\n"
        f"Available cells: {', '.join(sorted(valid_cells))}\n\n"
        f"Query: {query!r}\n\n"
        f"Reply with the cell name only, no explanation."
    )


def run_proof(
    *,
    oracle_dir: Path,
    hex_config: Path,
    treatment_share: float,
    treatment_enabled: bool,
) -> dict[str, Any]:
    """Run the A/B proof and return a structured result dict."""
    # Lazy imports so the script's --help works on a Profile S machine.
    from waggledance.application.services.hex_topology_registry import (
        HexTopologyRegistry,
    )
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )

    oracles = load_oracle_corpus(oracle_dir)
    reg = HexTopologyRegistry(config_path=str(hex_config), agents=[])
    valid_cells = set(reg.cells.keys())

    # Build BridgeLLMClient — honors Profile S env vars after PR #182.
    if treatment_enabled:
        client = BridgeLLMClient.default()
    else:
        client = BridgeLLMClient.disabled(reason="r21_1_treatment_disabled")

    harness = ABHarness(
        client=client,
        injection_point="hex.select_origin_cell",
        treatment_share=treatment_share,
        rng_seed=42,  # deterministic per-run sample partition
    )

    # We compute control quality directly (cheap, deterministic).
    control_route = reg.select_origin_cell

    # Treatment route: when share>0 and client enabled, ask BridgeLLMClient.
    treatment_local_llm_uses = 0
    treatment_fallthrough_uses = 0
    treatment_unparsed_responses = 0

    def treatment_route(query: str) -> str | None:
        nonlocal treatment_local_llm_uses
        nonlocal treatment_fallthrough_uses
        nonlocal treatment_unparsed_responses
        prompt = build_treatment_prompt(query, sorted(valid_cells))
        request = LLMRequest(
            injection_point="hex.select_origin_cell",
            prompt=prompt,
            intent="route_query",
        )
        # Run via harness so telemetry + budget are recorded.
        # treatment_share=1.0 ensures BOTH arms compute on every call.
        result = harness.run(
            control_fn=lambda: control_route(query),
            treatment_request=request,
        )
        if result.treatment_value is not None:
            parsed = parse_cell_from_llm_text(
                str(result.treatment_value), valid_cells
            )
            if parsed:
                treatment_local_llm_uses += 1
                return parsed
            treatment_unparsed_responses += 1
        # Fall-through: treatment unavailable / unparsed → control answer
        treatment_fallthrough_uses += 1
        return control_route(query)

    started = time.perf_counter()
    control_metrics = quality_arm(oracles, control_route)
    control_elapsed_s = time.perf_counter() - started

    started_t = time.perf_counter()
    treatment_metrics = quality_arm(oracles, treatment_route)
    treatment_elapsed_s = time.perf_counter() - started_t

    delta_quality_pct = (
        (treatment_metrics["quality"] - control_metrics["quality"])
        / control_metrics["quality"] * 100
        if control_metrics["quality"] > 0 else 0.0
    )

    # Local LLM availability surfaced in evidence per Decision 8
    from waggledance.core.bridge_llm.providers.ollama import OllamaProvider
    local_llm_status = (
        "available" if OllamaProvider().is_available() else "unavailable"
    )

    # Cloud (Anthropic) availability surfaced for R22.3 Profile L A/B:
    # distinguishes "no operator API key" from "LLM measurably worse" when
    # the treatment arm falls through to control. Only a missing SDK
    # (ImportError on a Profile S machine without the anthropic package) is
    # swallowed into "unavailable"; any other error from constructing the
    # provider or probing is_available() is a real regression and must
    # surface loudly rather than be masked as "unavailable".
    try:
        from waggledance.core.bridge_llm.providers.anthropic import (
            AnthropicProvider,
        )
    except ImportError:
        anthropic_status = "unavailable"
    else:
        anthropic_status = (
            "available" if AnthropicProvider().is_available() else "unavailable"
        )

    return {
        "schema_version": 1,
        "benchmark_id": "r21-oracle-ab-proof-2026-05-10",
        "task_id": "r21-claude-oracle-ab-harness-2026-05-10",
        "call_site": "HexTopologyRegistry.select_origin_cell",
        "corpus": {
            "oracle_dir": str(oracle_dir),
            "files": len(oracles),
            "total_positive": sum(len(o["positive"]) for o in oracles),
            "total_negative": sum(len(o["negative"]) for o in oracles),
        },
        "configuration": {
            "treatment_share": treatment_share,
            "treatment_enabled": treatment_enabled,
            "client_is_enabled": client.is_enabled(),
            "fallback_chain": list(client.fallback_chain),
            "local_llm_status": local_llm_status,
            "anthropic_status": anthropic_status,
            "rng_seed": 42,
        },
        "control": {
            "quality": control_metrics["quality"],
            "elapsed_seconds": round(control_elapsed_s, 4),
            "micro_pos": [
                control_metrics["micro_pos_correct"],
                control_metrics["micro_pos_total"],
            ],
            "micro_neg": [
                control_metrics["micro_neg_correct"],
                control_metrics["micro_neg_total"],
            ],
            "per_file": control_metrics["per_file"],
        },
        "treatment": {
            "quality": treatment_metrics["quality"],
            "elapsed_seconds": round(treatment_elapsed_s, 4),
            "local_llm_uses": treatment_local_llm_uses,
            "fallthrough_uses": treatment_fallthrough_uses,
            "unparsed_responses": treatment_unparsed_responses,
            "micro_pos": [
                treatment_metrics["micro_pos_correct"],
                treatment_metrics["micro_pos_total"],
            ],
            "micro_neg": [
                treatment_metrics["micro_neg_correct"],
                treatment_metrics["micro_neg_total"],
            ],
            "per_file": treatment_metrics["per_file"],
        },
        "delta_quality_pct": round(delta_quality_pct, 4),
        "deployment_recommendation": (
            "deploy_behind_flag"
            if delta_quality_pct >= 20.0 else "keep_disabled"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-json", type=Path, required=True,
        help="Write the proof result here as JSON.",
    )
    parser.add_argument(
        "--treatment-share", type=float, default=1.0,
        help=(
            "ABHarness treatment_share. Default 1.0 so the bench computes "
            "both arms on every call. Production wire-up keeps default 0.0 "
            "until results accepted."
        ),
    )
    parser.add_argument(
        "--treatment-enabled", action="store_true",
        help=(
            "Force treatment ON even when the resolved client config says "
            "off (used for testing). Default uses BridgeLLMClient.default() "
            "which honors WAGGLE_BRIDGE_LLM_ENABLED."
        ),
    )
    parser.add_argument(
        "--oracle-dir", type=Path, default=DEFAULT_ORACLE_DIR,
    )
    parser.add_argument(
        "--hex-config", type=Path, default=DEFAULT_HEX_CONFIG,
    )
    args = parser.parse_args()

    treatment_enabled = (
        args.treatment_enabled
        or os.environ.get("WAGGLE_BRIDGE_LLM_ENABLED") == "1"
    )

    result = run_proof(
        oracle_dir=args.oracle_dir,
        hex_config=args.hex_config,
        treatment_share=args.treatment_share,
        treatment_enabled=treatment_enabled,
    )

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"R21.1 oracle A/B proof — {args.out_json}")
    print(f"  control quality   : {result['control']['quality']}")
    print(f"  treatment quality : {result['treatment']['quality']}")
    print(f"  delta_quality_pct : {result['delta_quality_pct']:+.2f}%")
    print(f"  recommendation    : {result['deployment_recommendation']}")
    print(
        f"  treatment local_llm_uses={result['treatment']['local_llm_uses']} "
        f"fallthrough={result['treatment']['fallthrough_uses']} "
        f"unparsed={result['treatment']['unparsed_responses']} "
        f"local_llm_status={result['configuration']['local_llm_status']} "
        f"anthropic_status={result['configuration']['anthropic_status']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
