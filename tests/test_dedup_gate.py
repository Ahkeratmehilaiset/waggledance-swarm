"""Tests for hash-dedup in QualityGate and boilerplate filtering."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_gate():
    """Create a QualityGate with mocked dependencies."""
    from core.night_enricher import QualityGate
    consciousness = MagicMock()
    consciousness.embed.embed_query.return_value = [0.0] * 768
    consciousness.memory.search.return_value = []

    llm_validate = AsyncMock()
    resp = MagicMock()
    resp.error = False
    resp.content = "VALID"
    llm_validate.generate.return_value = resp

    return QualityGate(consciousness, llm_validate)


def test_hash_dedup_rejects_duplicate():
    """Hash-dedup step rejects exact duplicate text."""
    from core.night_enricher import EnrichmentCandidate, QualityGate

    gate = _make_gate()

    candidate = EnrichmentCandidate(
        text="Bees need water in summer",
        source_id="test")

    loop = asyncio.new_event_loop()
    # First check: should pass
    result1 = loop.run_until_complete(gate.check(candidate))
    assert result1.passed, f"First check should pass, got: {result1.reason}"

    # Second identical check: should be rejected by hash_dedup
    result2 = loop.run_until_complete(gate.check(candidate))
    assert not result2.passed, "Duplicate should be rejected"
    assert result2.step_failed == "hash_dedup"
    loop.close()


def test_hash_dedup_stats():
    """Hash-dedup step updates statistics."""
    from core.night_enricher import EnrichmentCandidate

    gate = _make_gate()
    candidate = EnrichmentCandidate(text="Test fact", source_id="test")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(gate.check(candidate))
    loop.run_until_complete(gate.check(candidate))
    loop.close()

    stats = gate.stats
    assert "hash_dedup" in stats
    assert stats["hash_dedup"]["checked"] == 2
    assert stats["hash_dedup"]["rejected"] == 1


def test_hash_dedup_case_insensitive():
    """Hash-dedup treats 'Hello' and 'hello' as duplicates."""
    from core.night_enricher import EnrichmentCandidate

    gate = _make_gate()

    loop = asyncio.new_event_loop()
    r1 = loop.run_until_complete(gate.check(
        EnrichmentCandidate(text="Bees are important", source_id="t")))
    r2 = loop.run_until_complete(gate.check(
        EnrichmentCandidate(text="BEES ARE IMPORTANT", source_id="t")))
    loop.close()

    assert r1.passed
    assert not r2.passed
    assert r2.step_failed == "hash_dedup"


def test_boilerplate_blacklist_exists():
    """Boilerplate blacklist file exists with expected markers."""
    bl_path = ROOT / "configs" / "boilerplate_blacklist.txt"
    assert bl_path.exists(), "boilerplate_blacklist.txt not found"
    content = bl_path.read_text(encoding="utf-8")
    assert "OLETUKSET JA KONTEKSTI" in content
    assert "ASSUMPTIONS AND CONTEXT" in content


def test_curated_no_boilerplate():
    """Curated JSONL should not contain boilerplate system messages."""
    curated = ROOT / "data" / "finetune_curated.jsonl"
    if not curated.exists():
        return  # Skip if no data

    import json
    boilerplate_count = 0
    with open(curated, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            for msg in entry.get("messages", []):
                if msg.get("role") == "system":
                    content = msg.get("content", "")
                    if "OLETUKSET JA KONTEKSTI" in content:
                        boilerplate_count += 1
    assert boilerplate_count == 0, f"Found {boilerplate_count} boilerplate system messages"


if __name__ == "__main__":
    tests = [
        test_hash_dedup_rejects_duplicate,
        test_hash_dedup_stats,
        test_hash_dedup_case_insensitive,
        test_boilerplate_blacklist_exists,
        test_curated_no_boilerplate,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
