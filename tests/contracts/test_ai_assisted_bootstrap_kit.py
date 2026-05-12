# SPDX-License-Identifier: BUSL-1.1
"""AI-Assisted Bootstrap Kit contract (strategic, ADR-062).

Substrate-only landing. Implementation of BootstrapKitLoader and
BootstrapKitGenerator is deferred. Pins kit schema + profile enum +
provenance + validation + signature requirements.
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "062-ai-assisted-bootstrap-kit.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "ai_assisted_bootstrap_kit.json"
)

REQUIRED_INVARIANT_IDS = {f"BSK-0{i:02d}" for i in range(1, 11)}
REQUIRED_PROFILES = {"GADGET", "COTTAGE", "HOME", "FACTORY"}
REQUIRED_AI_PROVIDERS = {"claude_opus_4_7", "openai_codex", "meta_consensus", "operator_authored"}
REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version", "profile", "provenance", "starter_solvers",
    "anti_features_seed", "anti_cargo_cult_probes_seed",
    "tunnel_overlay_seed", "training_distribution_hints", "validation_results",
}
REQUIRED_PROVENANCE_FIELDS = {
    "ai_provider", "ai_version", "generated_at_utc", "training_data_summary", "signature_hash",
}
REQUIRED_VALIDATION_FIELDS = {
    "anti_cargo_cult_pass_rate", "hot_path_budget_compliance",
    "l51_contract_check", "signed_off_by", "signed_off_at_utc",
}


def test_adr_062_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_strategic_positioning_documented() -> None:
    """ADR-062 must explicitly state the strategic positioning vs peer
    frameworks. This is what makes the leap competitive-grade."""
    text = ADR_PATH.read_text(encoding="utf-8").lower()
    assert "aider" in text or "cline" in text
    assert "autogen" in text or "crewai" in text
    assert "differentiates" in text or "competitive" in text


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_schema_version_constant() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["schema_version_constant"] == "bootstrap-kit-v1"


def test_profile_enum_matches() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["profile_enum"]) == REQUIRED_PROFILES


def test_ai_provider_enum_includes_claude_and_codex() -> None:
    """The whole point of this leap: world-class AI as bootstrap source.
    Claude Opus + OpenAI Codex MUST be in the provider enum."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    providers = set(c["ai_provider_enum"])
    assert "claude_opus_4_7" in providers
    assert "openai_codex" in providers


def test_required_top_level_keys_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["required_top_level_keys"]) == REQUIRED_TOP_LEVEL_KEYS


def test_required_provenance_fields_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["required_provenance_fields"]) == REQUIRED_PROVENANCE_FIELDS


def test_required_validation_fields_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["required_validation_fields"]) == REQUIRED_VALIDATION_FIELDS


def test_scale_targets_monotonic_across_profiles() -> None:
    """Kit scale (starter_solvers count, probes count, etc.) MUST be
    monotonic across profiles: GADGET <= COTTAGE <= HOME <= FACTORY.
    Same principle as ADR-055 PAB-002 profile budgets."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    scale = c["scale_targets_by_profile"]
    profile_order = ["GADGET", "COTTAGE", "HOME", "FACTORY"]
    for key in ["starter_solvers", "anti_features", "probes", "tunnels"]:
        prev_max = -1
        for prof in profile_order:
            current_min = scale[prof][key][0]
            assert current_min >= prev_max, (
                f"Scale {key} not monotonic across profiles: {prof} min {current_min} < prev max {prev_max}"
            )
            prev_max = scale[prof][key][1]


def test_invariants_match_required_set() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in c["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_no_runtime_ai_dependency_invariant() -> None:
    """BSK-008 is critical: loader is OFFLINE. AI providers are NOT
    called at runtime. Kit is static YAML. This keeps the production
    runtime independent of external AI availability."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    bsk008 = next((i for i in c["invariants"] if i["id"] == "BSK-008"), None)
    must_text = " ".join(bsk008.get("must", [])).lower()
    assert "no ai provider imports" in must_text or "offline" in must_text
    assert "deterministic" in must_text or "no live ai dependency" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_operator_signature_required_invariant() -> None:
    """BSK-007: even AI-generated kits need operator sign-off. Trust
    transfer through AI provenance, not blind acceptance."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    bsk007 = next((i for i in c["invariants"] if i["id"] == "BSK-007"), None)
    must_text = " ".join(bsk007.get("must", [])).lower()
    assert "operator" in must_text
    assert "sign" in must_text or "signed_off" in must_text


def test_strategic_capstone_marker() -> None:
    """Contract MUST mark itself as strategic_capstone (above the
    55-leap menu). Distinguishes this from substrate housekeeping."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c.get("strategic_capstone") is True
