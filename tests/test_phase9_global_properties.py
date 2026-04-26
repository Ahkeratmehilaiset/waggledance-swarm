"""Phase 9 GLOBAL PROPERTY TESTS — cross-phase invariants enforced by source-grep.

Per Prompt 1 §GLOBAL PROPERTY TESTS, these properties must hold across
all Phase 9 core modules together (not just within one phase):

- no silent failures
- no auto-enactment to main/live runtime
- no constitution self-mutation
- no foundational auto-promotion without human approval
- no capsule blast radius leakage
- deterministic builder request/result IDs
- no absolute path leakage in generated bundles
- no secrets in generated bundles

Each test below names which property it enforces.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


PHASE9_CORE_PACKAGES = (
    "autonomy",
    "ir",
    "capsules",
    "vector_identity",
    "ingestion",
    "world_model",
    "conversation",
    "identity",
    "provider_plane",
    "api_distillation",
    "builder_lane",
    "solver_synthesis",
    "memory_tiers",
    "hex_topology",
    "promotion",
    "proposal_compiler",
    "local_intelligence",
    "cross_capsule",
)


def _phase9_source_files() -> list[Path]:
    files: list[Path] = []
    for pkg in PHASE9_CORE_PACKAGES:
        d = ROOT / "waggledance" / "core" / pkg
        if d.exists():
            files.extend(d.rglob("*.py"))
    return files


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ═══════════════════ no silent failures ═══════════════════════════
#
# A silent failure pattern is any `except: pass` (bare or
# overly-broad) without a logged record. We allow `except Exception
# as e: ...` provided the body is not just `pass`.

_BARE_EXCEPT_PASS = re.compile(r"except\s*:\s*\n\s*pass", re.MULTILINE)
_BROAD_EXCEPT_PASS = re.compile(
    r"except\s+(BaseException|Exception)(\s+as\s+\w+)?\s*:\s*\n\s*pass",
    re.MULTILINE,
)


def test_no_bare_except_pass_in_phase9_core():
    """Property: no silent failures."""
    for p in _phase9_source_files():
        text = _read(p)
        assert not _BARE_EXCEPT_PASS.search(text), \
            f"{p.relative_to(ROOT)}: bare except: pass"
        assert not _BROAD_EXCEPT_PASS.search(text), \
            f"{p.relative_to(ROOT)}: broad except: pass"


# ═══════════════════ no auto-enactment to main/live runtime ════════

_FORBIDDEN_AUTO_ENACTMENT = (
    "git push origin main",
    "git push --force",
    "git push -f ",
    "promote_to_runtime(",
    "auto_apply_patch(",
    "merge_to_main(",
    "axiom_write(",
    "ollama.generate(",
    "openai.chat",
    "anthropic.messages",
    "requests.post(",
    "httpx.post(",
)


def test_no_auto_enactment_in_phase9_core():
    """Property: no auto-enactment to main/live runtime."""
    for p in _phase9_source_files():
        text = _read(p)
        for pat in _FORBIDDEN_AUTO_ENACTMENT:
            assert pat not in text, \
                f"{p.relative_to(ROOT)}: forbidden auto-enactment {pat!r}"


# ═══════════════════ no constitution self-mutation ════════════════

_CONSTITUTION_MUTATION_PATTERNS = (
    "constitution.yaml",  # mention is fine; we then grep for write ops near it
)


def test_no_constitution_write_in_phase9_core():
    """Property: no constitution self-mutation.

    No Phase 9 core module is allowed to .write_text or open(...) for
    write into constitution.yaml.
    """
    write_to_constitution = re.compile(
        r"(open\([^)]*constitution\.yaml[^)]*[\"']w[\"'b]?[\"']?\)|"
        r"constitution\.yaml.*\.write_text\(|"
        r"\.write_text\([^)]*constitution\.yaml)",
        re.DOTALL,
    )
    for p in _phase9_source_files():
        text = _read(p)
        assert not write_to_constitution.search(text), \
            f"{p.relative_to(ROOT)}: writes to constitution.yaml"


# ═══════════════════ no foundational auto-promotion ═══════════════
#
# Promotion to a runtime stage MUST require a human_approval_id.
# Source-grep verifies that the literal string 'human_approval_id'
# appears in the promotion package and that the literal
# 'no_runtime_auto_promotion=False' never appears anywhere in
# Phase 9 core.

def test_promotion_package_references_human_approval_id():
    """Property: no foundational auto-promotion without human approval."""
    pkg = ROOT / "waggledance" / "core" / "promotion"
    blob = "\n".join(_read(p) for p in pkg.glob("*.py"))
    assert "human_approval_id" in blob


def test_no_no_runtime_auto_promotion_false_anywhere_in_phase9():
    """Property: no foundational auto-promotion without human approval."""
    for p in _phase9_source_files():
        text = _read(p)
        assert "no_runtime_auto_promotion=False" not in text, \
            f"{p.relative_to(ROOT)}: no_runtime_auto_promotion=False"


def test_no_no_foundational_mutation_false_anywhere_in_phase9():
    for p in _phase9_source_files():
        text = _read(p)
        assert "no_foundational_mutation=False" not in text, \
            f"{p.relative_to(ROOT)}: no_foundational_mutation=False"


# ═══════════════════ no capsule blast radius leakage ══════════════
#
# Capsule registry/resolver enforces blast radius. The property is
# that NO Phase 9 core module bypasses the resolver by hard-coding a
# foreign-capsule path or by reaching into another capsule's runtime
# state.

_CAPSULE_BYPASS_PATTERNS = (
    "BlastRadiusViolation",  # mention is fine; ensure it is RAISED, not suppressed
)


def test_blast_radius_violation_is_raised_not_swallowed():
    """Property: no capsule blast radius leakage."""
    for p in _phase9_source_files():
        text = _read(p)
        if "BlastRadiusViolation" not in text:
            continue
        # Ensure the symbol is referenced for raising or class-def, not
        # silenced as `except BlastRadiusViolation: pass`.
        bad = re.compile(
            r"except\s+BlastRadiusViolation[^:]*:\s*\n\s*pass",
            re.MULTILINE,
        )
        assert not bad.search(text), \
            f"{p.relative_to(ROOT)}: BlastRadiusViolation suppressed"


# ═══════════════════ deterministic builder request/result IDs ═════

def test_builder_lane_uses_hashlib_for_ids():
    """Property: deterministic builder request/result IDs."""
    pkg = ROOT / "waggledance" / "core" / "builder_lane"
    if not pkg.exists():
        pytest.skip("builder_lane package missing")
    blob = "\n".join(_read(p) for p in pkg.glob("*.py"))
    assert "hashlib" in blob, "builder_lane should compute structural ids"
    # No use of uuid (non-deterministic) for id generation in core.
    assert "uuid.uuid4" not in blob, \
        "uuid.uuid4() is non-deterministic — use hashlib for ids"
    assert "uuid4()" not in blob


def test_no_uuid4_in_phase9_core():
    """Property: deterministic ids — uuid4 is forbidden in core."""
    for p in _phase9_source_files():
        text = _read(p)
        assert "uuid.uuid4" not in text, \
            f"{p.relative_to(ROOT)}: uuid.uuid4 (non-deterministic)"
        assert "uuid4()" not in text, \
            f"{p.relative_to(ROOT)}: uuid4()"


def test_no_random_in_phase9_core_for_ids():
    """Property: deterministic ids — `random` module is forbidden in core."""
    bad_imports = (
        "import random\n",
        "from random import",
        "random.random(",
        "random.randint(",
        "random.choice(",
    )
    for p in _phase9_source_files():
        text = _read(p)
        for pat in bad_imports:
            assert pat not in text, \
                f"{p.relative_to(ROOT)}: {pat!r}"


# ═══════════════════ no absolute path leakage in generated bundles ═

# Proposal compiler is the bundle producer. Verify its source does
# not embed absolute paths into generated artifacts. Allow
# Path.resolve() to exist (used at the boundary), but no string
# literal looking like a Windows drive letter or Unix absolute path
# in module-level constants.

_ABSOLUTE_PATH_LITERAL = re.compile(r"[\"']([A-Z]:[\\\\/]|/(home|Users|tmp|opt|var)/)")


def test_proposal_compiler_has_no_absolute_path_literals():
    """Property: no absolute path leakage in generated bundles."""
    pkg = ROOT / "waggledance" / "core" / "proposal_compiler"
    if not pkg.exists():
        pytest.skip("proposal_compiler missing")
    for p in pkg.glob("*.py"):
        text = _read(p)
        m = _ABSOLUTE_PATH_LITERAL.search(text)
        assert m is None, \
            f"{p.relative_to(ROOT)}: absolute path literal {m.group()!r}"


def test_phase9_core_has_no_drive_letter_literals():
    """Property: no absolute path leakage anywhere in Phase 9 core."""
    drive_letter = re.compile(r"[\"']([A-Z]:[\\\\/])")
    for p in _phase9_source_files():
        text = _read(p)
        m = drive_letter.search(text)
        assert m is None, \
            f"{p.relative_to(ROOT)}: drive-letter literal {m.group()!r}"


# ═══════════════════ no secrets in generated bundles ══════════════

_SECRET_PATTERNS = (
    "OPENAI_API_KEY=",
    "ANTHROPIC_API_KEY=",
    "AWS_SECRET_ACCESS_KEY=",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "ssh-rsa ",
    "Bearer ey",  # JWT-shaped tokens
)


def test_no_secret_literals_in_phase9_core():
    """Property: no secrets in generated bundles."""
    for p in _phase9_source_files():
        text = _read(p)
        for pat in _SECRET_PATTERNS:
            assert pat not in text, \
                f"{p.relative_to(ROOT)}: secret-looking literal {pat!r}"


# ═══════════════════ domain-neutrality (Phase 9 onward) ═══════════
#
# Per CLAUDE.md, new core code must use neutral terms. Adapter modules
# (e.g., from_hive, from_dream) are explicitly allowed because they
# bridge legacy Session B/C/D outputs. The check therefore EXCLUDES
# adapter directories.

_FORBIDDEN_METAPHORS = (
    "honeycomb",
    "beverage",
    "PDAM",
    " bee ",
    " hive ",
    " swarm ",
    " factory ",
)


def test_no_domain_metaphors_in_phase9_core_non_adapters():
    """Property: domain-neutrality in core, except adapter modules."""
    for p in _phase9_source_files():
        # Allow adapter dirs to reference legacy session names.
        if "adapters" in p.parts:
            continue
        # Allow the explicit per-phase-name file (from_*) which bridges.
        if p.name.startswith("from_"):
            continue
        text = _read(p).lower()
        for pat in _FORBIDDEN_METAPHORS:
            assert pat not in text, \
                f"{p.relative_to(ROOT)}: domain metaphor {pat!r}"
