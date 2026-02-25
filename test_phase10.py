#!/usr/bin/env python3
"""
Phase 10: Micro-Model Training — Verification Test
=====================================================
Tests all Phase 10 components:
  1. TrainingDataCollector — data curation from multiple sources
  2. PatternMatchEngine (V1) — regex + lookup table
  3. ClassifierModel (V2) — PyTorch neural network (graceful if no torch)
  4. LoRAModel (V3) — stub/framework (graceful if no peft)
  5. MicroModelOrchestrator — unified predict + training lifecycle
  6. Consciousness integration — before_llm() micro-model routing
  7. HiveMind integration — init, night cycle, get_status
  8. Settings — new config keys
  9. Dashboard — /api/micro_model endpoint
  10. Edge cases — missing data, missing deps
"""

import sys
import os
import json
import time
import re
import shutil
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent))

# Windows UTF-8
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; W = "\033[0m"
os.system("")  # ANSI Windows

results = {"pass": 0, "fail": 0, "warn": 0, "errors": []}


def OK(msg):
    results["pass"] += 1
    print(f"  {G}OK {msg}{W}")


def FAIL(msg):
    results["fail"] += 1
    results["errors"].append(msg)
    print(f"  {R}FAIL {msg}{W}")


def WARN(msg):
    results["warn"] += 1
    print(f"  {Y}WARN {msg}{W}")


def SECTION(title):
    print(f"\n{B}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{W}")


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def make_mock_consciousness():
    """Create a mock consciousness with memory and embed access."""
    c = MagicMock()
    c.memory = MagicMock()
    c.memory.count = 100
    c.memory.collection = MagicMock()
    c.memory.corrections = MagicMock()
    c.memory.corrections.count.return_value = 5
    c.memory.swarm_facts = MagicMock()
    c.memory.swarm_facts.count.return_value = 10
    c.memory.episodes = MagicMock()
    c.memory.episodes.count.return_value = 3
    c.embed = MagicMock()
    c.embed.available = True
    c.embed.embed_query = MagicMock(return_value=[0.1] * 768)
    c.embed.embed_batch = MagicMock(return_value=[[0.1] * 768] * 5)
    c.micro_model = None
    return c


def make_temp_data_dir():
    """Create a temp directory for test data."""
    return tempfile.mkdtemp(prefix="waggledance_test_")


def write_mock_finetune(data_dir, entries):
    """Write mock finetune_live.jsonl."""
    path = Path(data_dir) / "finetune_live.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ═══════════════════════════════════════════════════════════════
# 1. TRAINING DATA COLLECTOR
# ═══════════════════════════════════════════════════════════════
SECTION("1. TRAINING DATA COLLECTOR")

try:
    from core.training_collector import TrainingDataCollector
    OK("Import TrainingDataCollector")
except Exception as e:
    FAIL(f"Import TrainingDataCollector: {e}")

# 1a. Init
try:
    mock_c = make_mock_consciousness()
    tmp_dir = make_temp_data_dir()
    collector = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    assert collector._pairs == []
    assert collector._total_collected == 0
    OK("TrainingDataCollector init")
except Exception as e:
    FAIL(f"TrainingDataCollector init: {e}")

# 1b. collect_training_pair — accepts good pair
try:
    collector = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    result = collector.collect_training_pair(
        "mikä on varroa?",
        "Varroa destructor on mehiläisten ulkoloinen, joka aiheuttaa varroatauti.",
        "test", 0.90)
    assert result is True
    assert len(collector._pairs) == 1
    assert collector._total_collected == 1
    OK("collect_training_pair accepts good pair")
except Exception as e:
    FAIL(f"collect_training_pair good: {e}")

# 1c. collect_training_pair — rejects low confidence
try:
    result = collector.collect_training_pair(
        "joku kysymys?", "vastaus joka on riittävän pitkä testausta varten",
        "test", 0.50)
    assert result is False
    assert collector._total_rejected == 1
    OK("collect_training_pair rejects low confidence")
except Exception as e:
    FAIL(f"collect_training_pair low conf: {e}")

# 1d. collect_training_pair — rejects short answer
try:
    collector2 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    result = collector2.collect_training_pair("q?", "short", "test", 0.90)
    assert result is False
    OK("collect_training_pair rejects short answer")
except Exception as e:
    FAIL(f"collect_training_pair short: {e}")

# 1e. collect_training_pair — deduplication
try:
    collector3 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    collector3.collect_training_pair(
        "mikä on varroa?",
        "Varroa destructor on mehiläisten ulkoloinen, joka aiheuttaa varroatauti.",
        "test", 0.90)
    result = collector3.collect_training_pair(
        "Mikä on varroa?",  # Same question, different case
        "Varroa on toinen vastaus joka on riittävän pitkä.",
        "test", 0.85)
    assert result is False
    assert len(collector3._pairs) == 1
    OK("collect_training_pair deduplication")
except Exception as e:
    FAIL(f"collect_training_pair dedup: {e}")

# 1f. collect_from_finetune_live — parses jsonl
try:
    tmp2 = make_temp_data_dir()
    entries = [
        {"messages": [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Miten hoidetaan varroaa?"},
            {"role": "assistant", "content": "Varroa-hoito tehdään oksaalihapolla tai muurahaishapolla syksyllä. Käsittely ajoitetaan sikiöttömään aikaan."}
        ], "agent": "tautivahti", "timestamp": "2026-02-20T10:00:00"},
        {"messages": [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Lyhyt?"},
            {"role": "assistant", "content": "Kyllä"}  # Too short (<50 chars)
        ], "agent": "test", "timestamp": "2026-02-20T10:01:00"},
    ]
    write_mock_finetune(tmp2, entries)
    collector4 = TrainingDataCollector(mock_c, data_dir=tmp2)
    count = collector4.collect_from_finetune_live()
    assert count == 1, f"Expected 1, got {count}"
    assert len(collector4._pairs) == 1
    OK("collect_from_finetune_live parses and filters")
except Exception as e:
    FAIL(f"collect_from_finetune_live: {e}")

# 1g. collect_from_finetune_live — empty file
try:
    tmp3 = make_temp_data_dir()
    collector5 = TrainingDataCollector(mock_c, data_dir=tmp3)
    count = collector5.collect_from_finetune_live()
    assert count == 0
    OK("collect_from_finetune_live empty/missing → 0")
except Exception as e:
    FAIL(f"collect_from_finetune_live empty: {e}")

# 1h. collect_from_chromadb
try:
    mock_c2 = make_mock_consciousness()
    mock_c2.memory.collection.get.return_value = {
        "documents": [
            "Varroa destructor is a parasitic mite that affects honey bees worldwide.",
            "Short fact",  # Too short
        ],
        "metadatas": [
            {"confidence": 0.90, "validated": True},
            {"confidence": 0.90, "validated": True},
        ],
    }
    collector6 = TrainingDataCollector(mock_c2, data_dir=tmp_dir)
    count = collector6.collect_from_chromadb(min_confidence=0.80)
    assert count >= 1
    OK("collect_from_chromadb filters and creates pairs")
except Exception as e:
    FAIL(f"collect_from_chromadb: {e}")

# 1i. collect_from_corrections
try:
    mock_c3 = make_mock_consciousness()
    mock_c3.memory.corrections.count.return_value = 2
    mock_c3.memory.corrections.get.return_value = {
        "documents": [
            "Q: What is the varroa threshold? BAD: 5 mites GOOD: The varroa treatment threshold is typically 3 mites per 100 bees in Finland",
        ],
        "metadatas": [{}],
    }
    collector7 = TrainingDataCollector(mock_c3, data_dir=tmp_dir)
    count = collector7.collect_from_corrections()
    assert count >= 1
    OK("collect_from_corrections extracts Q/GOOD pairs")
except Exception as e:
    FAIL(f"collect_from_corrections: {e}")

# 1j. get_training_data — not enough
try:
    collector8 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    collector8.collect_training_pair(
        "test q?", "A long enough answer for testing purposes in WaggleDance",
        "test", 0.90)
    result = collector8.get_training_data(min_pairs=100)
    assert result is None
    OK("get_training_data returns None if < min_pairs")
except Exception as e:
    FAIL(f"get_training_data min: {e}")

# 1k. get_training_data — enough
try:
    collector9 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    for i in range(15):
        collector9.collect_training_pair(
            f"question {i}?",
            f"Answer number {i} which is long enough for testing purposes here",
            "test", 0.85)
    result = collector9.get_training_data(min_pairs=10)
    assert result is not None
    assert len(result) == 15
    OK("get_training_data returns pairs when enough")
except Exception as e:
    FAIL(f"get_training_data enough: {e}")

# 1l. export_for_v1
try:
    collector10 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    collector10.collect_training_pair(
        "mikä on varroa?",
        "Varroa destructor on mehiläisten ulkoloinen.",
        "test", 0.95)
    collector10.collect_training_pair(
        "low confidence question?",
        "This answer should not appear in V1 export because conf is too low",
        "test", 0.80)
    v1_data = collector10.export_for_v1()
    assert len(v1_data) == 1
    assert v1_data[0]["confidence"] >= 0.90
    OK("export_for_v1 filters by confidence >= 0.90")
except Exception as e:
    FAIL(f"export_for_v1: {e}")

# 1m. export_for_v2
try:
    collector11 = TrainingDataCollector(mock_c, data_dir=tmp_dir)
    for i in range(12):
        collector11.collect_training_pair(
            f"question {i}?",
            f"Answer {i % 3} which is long enough for testing",
            "test", 0.85)
    v2_data = collector11.export_for_v2()
    assert v2_data is not None
    assert "questions" in v2_data
    assert "answer_ids" in v2_data
    assert "answers" in v2_data
    assert len(v2_data["questions"]) == 12
    OK("export_for_v2 groups answers into classes")
except Exception as e:
    FAIL(f"export_for_v2: {e}")

# 1n. stats property
try:
    s = collector11.stats
    assert "total_pairs" in s
    assert "total_collected" in s
    assert "total_rejected" in s
    assert "sources" in s
    OK("TrainingDataCollector stats property")
except Exception as e:
    FAIL(f"stats: {e}")

# 1o. save_pairs
try:
    collector11.save_pairs()
    saved_path = Path(tmp_dir) / "training_pairs.jsonl"
    assert saved_path.exists()
    lines = saved_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 12
    OK("save_pairs writes to jsonl")
except Exception as e:
    FAIL(f"save_pairs: {e}")

# 1p. reset
try:
    collector11.reset()
    assert len(collector11._pairs) == 0
    assert collector11._total_collected == 0
    OK("reset clears all state")
except Exception as e:
    FAIL(f"reset: {e}")


# ═══════════════════════════════════════════════════════════════
# 2. PATTERN MATCH ENGINE (V1)
# ═══════════════════════════════════════════════════════════════
SECTION("2. PATTERN MATCH ENGINE (V1)")

try:
    from core.micro_model import PatternMatchEngine
    OK("Import PatternMatchEngine")
except Exception as e:
    FAIL(f"Import PatternMatchEngine: {e}")

# 2a. Init
try:
    v1_dir = make_temp_data_dir()
    v1 = PatternMatchEngine(data_dir=v1_dir)
    assert v1._hits == 0
    assert v1._misses == 0
    assert len(v1._lookup) == 0
    OK("V1 init (empty)")
except Exception as e:
    FAIL(f"V1 init: {e}")

# 2b. normalize Finnish text
try:
    key = v1._normalize("Mikä on varroa-kynnys?")
    assert "?" not in key
    assert key == key.lower()
    key2 = v1._normalize("mikä on VARROA-kynnys")
    assert key == key2, f"'{key}' != '{key2}'"
    OK("V1 _normalize Finnish text")
except Exception as e:
    FAIL(f"V1 _normalize: {e}")

# 2c. Train with pairs
try:
    pairs = [
        {"pattern": "mikä on varroa?", "answer": "Varroa destructor on loinen.", "confidence": 0.95},
        {"pattern": "miten hoitaa varroaa?", "answer": "Oksaalihappo tai muurahaishappo.", "confidence": 0.92},
        {"pattern": "low conf question", "answer": "Should not be included", "confidence": 0.80},
    ]
    v1.train(pairs)
    assert len(v1._lookup) >= 2  # Only >= 0.90 confidence
    assert (v1._data_dir / "patterns.json").exists()
    OK("V1 train builds lookups + patterns + saves")
except Exception as e:
    FAIL(f"V1 train: {e}")

# 2d. Predict exact match
try:
    result = v1.predict("mikä on varroa?")
    assert result is not None
    assert "varroa" in result["answer"].lower() or "loinen" in result["answer"].lower()
    assert result["confidence"] > 0.85
    assert result["method"].startswith("v1")
    OK("V1 predict exact match")
except Exception as e:
    FAIL(f"V1 predict exact: {e}")

# 2e. Predict regex match
try:
    result = v1.predict("kerro varroa hoidosta")
    if result is not None:
        assert result["method"] in ("v1_exact", "v1_regex")
        OK("V1 predict regex match")
    else:
        WARN("V1 regex did not match (acceptable — depends on key words)")
except Exception as e:
    FAIL(f"V1 predict regex: {e}")

# 2f. No match returns None
try:
    result = v1.predict("mikä on sähkön hinta tänään?")
    assert result is None
    OK("V1 predict no match returns None")
except Exception as e:
    FAIL(f"V1 no match: {e}")

# 2g. Empty input
try:
    result = v1.predict("")
    assert result is None
    result = v1.predict("   ")
    assert result is None
    OK("V1 predict empty input returns None")
except Exception as e:
    FAIL(f"V1 empty: {e}")

# 2h. Save/load patterns
try:
    v1_dir2 = make_temp_data_dir()
    v1a = PatternMatchEngine(data_dir=v1_dir2)
    v1a.train([
        {"pattern": "testi kysymys?", "answer": "Testi vastaus joka on tarpeeksi pitkä.", "confidence": 0.95},
    ])
    # Load in new instance
    v1b = PatternMatchEngine(data_dir=v1_dir2)
    assert len(v1b._lookup) >= 1
    result = v1b.predict("testi kysymys?")
    assert result is not None
    OK("V1 save/load patterns roundtrip")
except Exception as e:
    FAIL(f"V1 save/load: {e}")

# 2i. Stats
try:
    s = v1.stats
    assert "lookup_count" in s
    assert "pattern_count" in s
    assert "hits" in s
    assert "misses" in s
    assert "hit_rate" in s
    OK("V1 stats property")
except Exception as e:
    FAIL(f"V1 stats: {e}")


# ═══════════════════════════════════════════════════════════════
# 3. CLASSIFIER MODEL (V2)
# ═══════════════════════════════════════════════════════════════
SECTION("3. CLASSIFIER MODEL (V2)")

try:
    from core.micro_model import ClassifierModel
    OK("Import ClassifierModel")
except Exception as e:
    FAIL(f"Import ClassifierModel: {e}")

# 3a. Init
try:
    v2_path = Path(make_temp_data_dir()) / "test_v2.pt"
    v2 = ClassifierModel(consciousness=mock_c, model_path=str(v2_path))
    assert v2._available is False  # No model loaded yet
    OK("V2 init (no model)")
except Exception as e:
    FAIL(f"V2 init: {e}")

# 3b. Check torch availability
try:
    has_torch = v2._torch_available
    if has_torch:
        OK("V2 torch available")
    else:
        WARN("V2 torch not installed (V2 features limited)")
except Exception as e:
    FAIL(f"V2 torch check: {e}")

# 3c. Predict returns None when unavailable
try:
    result = v2.predict("test question")
    assert result is None
    OK("V2 predict returns None when unavailable")
except Exception as e:
    FAIL(f"V2 predict unavailable: {e}")

# 3d. Train + predict (if torch available)
try:
    if v2._torch_available:
        import torch
        # Create mock training data with embeddings
        num_samples = 50
        num_classes = 5
        embed_dim = 768
        embeddings = torch.randn(num_samples, embed_dim).tolist()
        answer_ids = [i % num_classes for i in range(num_samples)]
        answers = [f"Answer class {i} is a detailed answer for testing" for i in range(num_classes)]
        questions = [f"Question {i}?" for i in range(num_samples)]

        training_data = {
            "questions": questions,
            "answer_ids": answer_ids,
            "answers": answers,
            "embeddings": embeddings,
        }

        result = v2.train(training_data, epochs=30, lr=0.01)
        assert result is True
        assert v2._available is True
        assert v2._num_classes == num_classes
        assert v2._generation == 1
        OK("V2 train succeeds with torch")

        # Predict with a known embedding
        pred = v2.predict("test", embedding=embeddings[0])
        if pred is not None:
            assert "answer" in pred
            assert "confidence" in pred
            assert pred["method"] == "v2_classifier"
            OK("V2 predict returns result after training")
        else:
            WARN("V2 predict returned None (low confidence, acceptable)")
    else:
        result = v2.train({"questions": [], "answer_ids": [], "answers": [], "embeddings": []})
        assert result is False
        OK("V2 train graceful without torch")
except Exception as e:
    FAIL(f"V2 train+predict: {e}")

# 3e. Save/load (if torch available)
try:
    if v2._torch_available and v2._available:
        v2b = ClassifierModel(consciousness=mock_c, model_path=str(v2_path))
        assert v2b._available is True
        assert v2b._num_classes == v2._num_classes
        OK("V2 save/load roundtrip")
    else:
        OK("V2 save/load skipped (no torch)")
except Exception as e:
    FAIL(f"V2 save/load: {e}")

# 3f. Stats
try:
    s = v2.stats
    assert "available" in s
    assert "torch_available" in s
    assert "generation" in s
    assert "num_classes" in s
    assert "hits" in s
    assert "misses" in s
    assert "hit_rate" in s
    OK("V2 stats property")
except Exception as e:
    FAIL(f"V2 stats: {e}")


# ═══════════════════════════════════════════════════════════════
# 4. LORA MODEL (V3 — stub)
# ═══════════════════════════════════════════════════════════════
SECTION("4. LORA MODEL (V3 — stub)")

try:
    from core.micro_model import LoRAModel
    OK("Import LoRAModel")
except Exception as e:
    FAIL(f"Import LoRAModel: {e}")

# 4a. Init
try:
    v3_dir = make_temp_data_dir()
    v3 = LoRAModel(data_dir=v3_dir)
    assert v3._available is False
    assert v3._generation == 0
    OK("V3 init")
except Exception as e:
    FAIL(f"V3 init: {e}")

# 4b. Graceful without peft
try:
    # Most environments won't have peft
    if not v3._peft_available:
        OK("V3 peft not available (expected, graceful)")
    else:
        WARN("V3 peft available (unexpected in test env)")
except Exception as e:
    FAIL(f"V3 peft check: {e}")

# 4c. Predict returns None when unavailable
try:
    result = v3.predict("test question")
    assert result is None
    OK("V3 predict returns None when unavailable")
except Exception as e:
    FAIL(f"V3 predict: {e}")

# 4d. Train stub
try:
    pairs = [{"question": "q?", "answer": "a"} for _ in range(10)]
    result = v3.train(pairs)
    if v3._peft_available:
        assert result is True
        OK("V3 train with peft")
    else:
        assert result is False
        OK("V3 train graceful without peft")
except Exception as e:
    FAIL(f"V3 train: {e}")

# 4e. Stats
try:
    s = v3.stats
    assert "available" in s
    assert "peft_available" in s
    assert "generation" in s
    assert "model_name" in s
    assert "hits" in s
    assert "misses" in s
    OK("V3 stats property")
except Exception as e:
    FAIL(f"V3 stats: {e}")


# ═══════════════════════════════════════════════════════════════
# 5. MICRO-MODEL ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════
SECTION("5. MICRO-MODEL ORCHESTRATOR")

try:
    from core.micro_model import MicroModelOrchestrator
    OK("Import MicroModelOrchestrator")
except Exception as e:
    FAIL(f"Import MicroModelOrchestrator: {e}")

# 5a. Init
try:
    mock_c5 = make_mock_consciousness()
    tmp5 = make_temp_data_dir()
    collector5 = TrainingDataCollector(mock_c5, data_dir=tmp5)
    orch = MicroModelOrchestrator(mock_c5, collector5, data_dir=tmp5)
    assert orch._training_count == 0
    assert orch._last_train_cycle == 0
    OK("Orchestrator init")
except Exception as e:
    FAIL(f"Orchestrator init: {e}")

# 5b. Predict with no trained models → None
try:
    # V1 has no patterns, V2 has no trained model → should return None
    assert orch.v1.stats["lookup_count"] == 0
    assert orch.v2._available is False
    result = orch.predict("joku tuntematon kysymys tässä?")
    assert result is None, f"Expected None, got {result}"
    OK("Orchestrator predict returns None (no models trained)")
except (Exception, AssertionError) as e:
    FAIL(f"Orchestrator predict empty: {e}")

# 5c. Predict tries V1 first
try:
    # Train V1 with some patterns
    orch.v1.train([
        {"pattern": "mikä on varroa?",
         "answer": "Varroa destructor on loinen.",
         "confidence": 0.95},
    ])
    result = orch.predict("mikä on varroa?")
    assert result is not None
    assert result["method"].startswith("v1")
    OK("Orchestrator predict tries V1 first")
except Exception as e:
    FAIL(f"Orchestrator V1 priority: {e}")

# 5d. is_training_due
try:
    # 0 - 0 = 0, 0 >= 50 is False
    assert orch.is_training_due(0) is False
    assert orch.is_training_due(49) is False
    assert orch.is_training_due(50) is True
    orch._last_train_cycle = 50
    assert orch.is_training_due(99) is False
    assert orch.is_training_due(100) is True
    orch._last_train_cycle = 0  # Reset
    OK("Orchestrator is_training_due")
except Exception as e:
    FAIL(f"is_training_due: {e}")

# 5e. maybe_train triggers collector + training
try:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    tmp5b = make_temp_data_dir()
    # Write mock finetune data
    entries = []
    for i in range(20):
        entries.append({
            "messages": [
                {"role": "system", "content": "System"},
                {"role": "user", "content": f"Unique question number {i} about bees?"},
                {"role": "assistant",
                 "content": f"Answer {i}: A detailed response about beekeeping topic number {i} with enough text."}
            ],
            "agent": "test", "timestamp": "2026-02-20T10:00:00"
        })
    write_mock_finetune(tmp5b, entries)

    mock_c5b = make_mock_consciousness()
    # Mock chromadb to return empty (simplify test)
    mock_c5b.memory.collection.get.return_value = {"documents": [], "metadatas": []}
    mock_c5b.memory.corrections.count.return_value = 0

    collector5b = TrainingDataCollector(mock_c5b, data_dir=tmp5b)
    orch2 = MicroModelOrchestrator(mock_c5b, collector5b, data_dir=tmp5b)

    loop.run_until_complete(orch2.maybe_train(50))
    assert orch2._training_count == 1
    assert orch2._last_train_cycle == 50
    OK("Orchestrator maybe_train triggers training")
    loop.close()
except Exception as e:
    FAIL(f"maybe_train: {e}")

# 5f. Combined stats
try:
    s = orch.stats
    assert "training_count" in s
    assert "v1" in s
    assert "v2" in s
    assert "v3" in s
    assert "collector" in s
    OK("Orchestrator stats property")
except Exception as e:
    FAIL(f"Orchestrator stats: {e}")


# ═══════════════════════════════════════════════════════════════
# 6. CONSCIOUSNESS INTEGRATION
# ═══════════════════════════════════════════════════════════════
SECTION("6. CONSCIOUSNESS INTEGRATION")

# 6a. micro_model attribute exists
try:
    from consciousness import Consciousness
    # Check __init__ sets micro_model
    import inspect
    src = inspect.getsource(Consciousness.__init__)
    assert "micro_model" in src
    OK("Consciousness.__init__ has micro_model attribute")
except Exception as e:
    FAIL(f"consciousness micro_model attr: {e}")

# 6b. before_llm has micro_model check
try:
    src = inspect.getsource(Consciousness.before_llm)
    assert "micro_model" in src
    assert "MicroModel" in src
    assert "micro_" in src
    OK("before_llm() has micro-model routing")
except Exception as e:
    FAIL(f"before_llm micro_model: {e}")

# 6c. before_llm routes through micro_model
try:
    from consciousness import Consciousness, PreFilterResult

    # Create a minimal consciousness mock that simulates the routing
    # We'll check that micro_model.predict is called before hot_cache
    mock_mm = MagicMock()
    mock_mm.predict.return_value = {
        "answer": "Testi vastaus mikromallista",
        "confidence": 0.95,
        "method": "v1_exact",
    }

    # We need to verify the source code order: micro_model before hot_cache
    lines = src.split("\n")
    micro_idx = None
    hot_cache_idx = None
    for i, line in enumerate(lines):
        if "micro_model" in line and "predict" in line:
            micro_idx = i
        if "hot_cache" in line and "get" in line and micro_idx is not None:
            hot_cache_idx = i
            break
    if micro_idx is not None and hot_cache_idx is not None:
        assert micro_idx < hot_cache_idx
        OK("before_llm checks micro_model BEFORE hot_cache")
    else:
        WARN("Could not verify micro_model vs hot_cache order in source")
except Exception as e:
    FAIL(f"before_llm routing order: {e}")

# 6d. PreFilterResult method starts with "micro_"
try:
    # Check source for method=f"micro_"
    assert 'method=f"micro_' in src or "method=f\"micro_" in src
    OK("PreFilterResult method format: micro_{method}")
except Exception as e:
    FAIL(f"PreFilterResult method: {e}")

# 6e. stats includes micro_model
try:
    stats_src = inspect.getsource(Consciousness.stats.fget)
    assert "micro_model" in stats_src
    OK("Consciousness.stats includes micro_model")
except Exception as e:
    FAIL(f"stats micro_model: {e}")


# ═══════════════════════════════════════════════════════════════
# 7. HIVEMIND INTEGRATION
# ═══════════════════════════════════════════════════════════════
SECTION("7. HIVEMIND INTEGRATION")

# 7a. HiveMind has micro_model attribute
try:
    from hivemind import HiveMind
    hm_src = inspect.getsource(HiveMind.__init__)
    assert "self.micro_model" in hm_src
    assert "self.training_collector" in hm_src
    OK("HiveMind.__init__ has micro_model + training_collector")
except Exception as e:
    FAIL(f"HiveMind attrs: {e}")

# 7b. _init_learning_engines has micro_model init
try:
    init_src = inspect.getsource(HiveMind._init_learning_engines)
    assert "micro_model" in init_src
    assert "MicroModelOrchestrator" in init_src
    assert "TrainingDataCollector" in init_src
    assert "consciousness.micro_model" in init_src
    OK("_init_learning_engines inits micro_model")
except Exception as e:
    FAIL(f"_init_learning_engines: {e}")

# 7c. _night_learning_cycle has training check
try:
    night_src = inspect.getsource(HiveMind._night_learning_cycle)
    assert "micro_model" in night_src
    assert "maybe_train" in night_src
    assert "micro_training" in night_src
    OK("_night_learning_cycle has micro-model training")
except Exception as e:
    FAIL(f"_night_learning_cycle: {e}")

# 7d. get_status includes micro_model
try:
    status_src = inspect.getsource(HiveMind.get_status)
    assert "micro_model" in status_src
    OK("get_status includes micro_model")
except Exception as e:
    FAIL(f"get_status: {e}")


# ═══════════════════════════════════════════════════════════════
# 8. SETTINGS
# ═══════════════════════════════════════════════════════════════
SECTION("8. SETTINGS CONFIGURATION")

try:
    import yaml
    with open("configs/settings.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    al = cfg.get("advanced_learning", {})

    assert "micro_model_enabled" in al
    assert al["micro_model_enabled"] is True
    OK("micro_model_enabled = true")

    assert "micro_model_v2_enabled" in al
    assert al["micro_model_v2_enabled"] is True
    OK("micro_model_v2_enabled = true")

    assert "micro_model_v3_enabled" in al
    assert al["micro_model_v3_enabled"] is False
    OK("micro_model_v3_enabled = false")

    assert "micro_model_training_interval" in al
    assert al["micro_model_training_interval"] == 50
    OK("micro_model_training_interval = 50")

    assert "micro_model_min_pairs" in al
    assert al["micro_model_min_pairs"] == 100
    OK("micro_model_min_pairs = 100")

except Exception as e:
    FAIL(f"Settings: {e}")


# ═══════════════════════════════════════════════════════════════
# 9. DASHBOARD
# ═══════════════════════════════════════════════════════════════
SECTION("9. DASHBOARD ENDPOINTS")

# 9a. /api/micro_model endpoint exists
try:
    dashboard_src = Path("web/dashboard.py").read_text(encoding="utf-8")
    assert "/api/micro_model" in dashboard_src
    assert "micro_model_stats" in dashboard_src
    OK("/api/micro_model endpoint defined")
except Exception as e:
    FAIL(f"/api/micro_model: {e}")

# 9b. /api/consciousness includes micro_model
try:
    # Find the consciousness_stats function and check micro_model is in it
    cs_start = dashboard_src.index("def consciousness_stats")
    # Find the next function definition after it
    next_func = dashboard_src.index("\n    @app.", cs_start + 1)
    consciousness_section = dashboard_src[cs_start:next_func]
    assert "micro_model" in consciousness_section
    OK("/api/consciousness includes micro_model")
except Exception as e:
    FAIL(f"/api/consciousness micro_model: {e}")

# 9c. WS event handler pattern
try:
    # Check hivemind sends micro_training WS event
    hm_full = Path("hivemind.py").read_text(encoding="utf-8")
    assert '"micro_training"' in hm_full
    OK("WS micro_training event exists")
except Exception as e:
    FAIL(f"WS micro_training: {e}")


# ═══════════════════════════════════════════════════════════════
# 10. EDGE CASES
# ═══════════════════════════════════════════════════════════════
SECTION("10. EDGE CASES")

# 10a. No training data → no training
try:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    empty_dir = make_temp_data_dir()
    mock_c10 = make_mock_consciousness()
    mock_c10.memory.count = 0
    mock_c10.memory.collection.get.return_value = {"documents": [], "metadatas": []}
    mock_c10.memory.corrections.count.return_value = 0
    mock_c10.memory.corrections.get.return_value = {"documents": [], "metadatas": []}
    coll10 = TrainingDataCollector(mock_c10, data_dir=empty_dir)
    orch10 = MicroModelOrchestrator(mock_c10, coll10, data_dir=empty_dir)

    loop.run_until_complete(orch10.maybe_train(50))
    # Should complete without error, V1 should have no patterns
    assert orch10._training_count == 1, f"training_count={orch10._training_count}"
    assert orch10.v1.stats["lookup_count"] == 0, f"lookups={orch10.v1.stats['lookup_count']}"
    OK("No training data → trains with 0 patterns (no crash)")
    loop.close()
except (Exception, AssertionError) as e:
    FAIL(f"No data training: {e}")

# 10b. torch missing → V2 unavailable but V1 works
try:
    v1_temp = make_temp_data_dir()
    v1_test = PatternMatchEngine(data_dir=v1_temp)
    v1_test.train([
        {"pattern": "test?", "answer": "Works without torch at all.", "confidence": 0.95},
    ])
    result = v1_test.predict("test?")
    assert result is not None
    # V2 without torch
    v2_test = ClassifierModel(consciousness=None)
    if not v2_test._torch_available:
        assert v2_test._available is False
        result2 = v2_test.predict("test?")
        assert result2 is None
        OK("V1 works, V2 graceful without torch")
    else:
        OK("V1 works, V2 has torch (both work)")
except Exception as e:
    FAIL(f"V1 works V2 graceful: {e}")

# 10c. Empty finetune_live → 0 pairs
try:
    empty_dir2 = make_temp_data_dir()
    # Create empty file
    (Path(empty_dir2) / "finetune_live.jsonl").write_text("")
    coll10c = TrainingDataCollector(mock_c, data_dir=empty_dir2)
    count = coll10c.collect_from_finetune_live()
    assert count == 0
    OK("Empty finetune_live.jsonl → 0 pairs")
except Exception as e:
    FAIL(f"Empty finetune: {e}")

# 10d. Malformed jsonl lines → skipped gracefully
try:
    bad_dir = make_temp_data_dir()
    with open(Path(bad_dir) / "finetune_live.jsonl", "w") as f:
        f.write("not valid json\n")
        f.write('{"messages": "wrong format"}\n')
        f.write(json.dumps({
            "messages": [
                {"role": "system", "content": "ok"},
                {"role": "user", "content": "valid question here?"},
                {"role": "assistant",
                 "content": "Valid answer that is long enough for the test threshold check."}
            ], "agent": "test"
        }) + "\n")
    coll10d = TrainingDataCollector(mock_c, data_dir=bad_dir)
    count = coll10d.collect_from_finetune_live()
    assert count == 1  # Only the valid one
    OK("Malformed jsonl lines skipped gracefully")
except Exception as e:
    FAIL(f"Malformed jsonl: {e}")

# 10e. V1 predict performance (should be very fast)
try:
    v1_perf = PatternMatchEngine(data_dir=make_temp_data_dir())
    # Train with 50 patterns
    big_pairs = [
        {"pattern": f"kysymys numero {i} mehiläishoidosta?",
         "answer": f"Vastaus {i}: Tämä on yksityiskohtainen vastaus mehiläishoitoon.",
         "confidence": 0.95}
        for i in range(50)
    ]
    v1_perf.train(big_pairs)
    assert v1_perf.stats["lookup_count"] == 50

    # Time prediction
    t0 = time.perf_counter()
    for i in range(100):
        v1_perf.predict(f"kysymys numero {i % 50} mehiläishoidosta?")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    per_query_ms = elapsed_ms / 100

    if per_query_ms < 1.0:
        OK(f"V1 performance: {per_query_ms:.3f}ms per query (< 1ms)")
    elif per_query_ms < 5.0:
        WARN(f"V1 performance: {per_query_ms:.3f}ms per query (< 5ms, acceptable)")
    else:
        FAIL(f"V1 performance: {per_query_ms:.3f}ms per query (too slow)")
except Exception as e:
    FAIL(f"V1 performance: {e}")

# 10f. Orchestrator predict order (V1 before V2)
try:
    _tmp10f = make_temp_data_dir()
    mock_c10f = make_mock_consciousness()
    coll10f = TrainingDataCollector(mock_c10f, data_dir=_tmp10f)
    orch10f = MicroModelOrchestrator(mock_c10f, coll10f, data_dir=_tmp10f)

    # Mock V1 to return a result
    orch10f.v1 = MagicMock()
    orch10f.v1.predict = MagicMock(return_value={
        "answer": "V1 answer", "confidence": 0.95, "method": "v1_exact"})
    # Mock V2
    orch10f.v2 = MagicMock()
    orch10f.v2._available = True
    orch10f.v2.predict = MagicMock(return_value={
        "answer": "V2 answer", "confidence": 0.90, "method": "v2_classifier"})

    result = orch10f.predict("test?")
    assert result is not None
    assert result["method"] == "v1_exact"
    # V2 should not have been called because V1 returned confident result
    orch10f.v2.predict.assert_not_called()
    OK("Orchestrator predict order: V1 before V2")
except Exception as e:
    FAIL(f"Predict order: {e}")

# 10g. Orchestrator falls through to V2 when V1 misses
try:
    orch10f.v1.predict.return_value = None
    result = orch10f.predict("test?")
    assert result is not None
    assert result["method"] == "v2_classifier"
    OK("Orchestrator falls through to V2 when V1 misses")
except Exception as e:
    FAIL(f"V1→V2 fallthrough: {e}")

# 10h. _normalize_question for collector dedup
try:
    coll_norm = TrainingDataCollector(mock_c, data_dir=make_temp_data_dir())
    n1 = coll_norm._normalize_question("Mikä on varroa?")
    n2 = coll_norm._normalize_question("mikä on varroa")
    n3 = coll_norm._normalize_question("  Mikä on VARROA???  ")
    assert n1 == n2 == n3
    OK("Collector _normalize_question consistent")
except Exception as e:
    FAIL(f"Collector normalize: {e}")

# 10i. micro_model disabled in settings → no errors
try:
    # Check that hivemind checks config before init
    init_src = inspect.getsource(HiveMind._init_learning_engines)
    assert 'micro_model_enabled' in init_src
    OK("_init_learning_engines checks micro_model_enabled config")
except Exception as e:
    FAIL(f"Config check: {e}")


# ═══════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════
# Temp dirs are cleaned by OS on reboot; safe to leave


# ═══════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n{B}{'='*60}")
print(f"  PHASE 10 TEST SUMMARY")
print(f"{'='*60}{W}")
print(f"  {G}PASS: {results['pass']}{W}")
print(f"  {R}FAIL: {results['fail']}{W}")
print(f"  {Y}WARN: {results['warn']}{W}")

if results["errors"]:
    print(f"\n{R}FAILURES:{W}")
    for e in results["errors"]:
        print(f"  {R}  - {e}{W}")

total = results["pass"] + results["fail"]
pct = (results["pass"] / total * 100) if total > 0 else 0
print(f"\n  Score: {results['pass']}/{total} ({pct:.0f}%)")

if results["fail"] == 0:
    print(f"\n  {G}{'='*50}")
    print(f"  PHASE 10 COMPLETE")
    print(f"  Micro-Model Training: V1 pattern match, V2 classifier,")
    print(f"  V3 LoRA stub, orchestrator, smart router integration")
    print(f"  {'='*50}{W}")
else:
    print(f"\n  {R}{'='*50}")
    print(f"  {results['fail']} test(s) failed - review above")
    print(f"  {'='*50}{W}")
    sys.exit(1)
