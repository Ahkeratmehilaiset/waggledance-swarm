"""Tests for V1 PatternMatchEngine training from curated data."""
import json
import tempfile
import os
import sys
from pathlib import Path

# Add project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_train_from_pairs():
    """V1 train() creates lookups and patterns from Q/A pairs."""
    from core.micro_model import PatternMatchEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = PatternMatchEngine(data_dir=tmp, load_configs=False)

        pairs = [
            {"question": "Miten varroapunkkia torjutaan?",
             "answer": "Oksaalihappohoito on tehokas varroapunkin torjuntakeino.",
             "confidence": 0.95},
            {"question": "Milloin hunajaa voi lypsää?",
             "answer": "Hunajan lypsy aloitetaan kun kehät ovat vähintään 80% sinetöityjä.",
             "confidence": 0.92},
            {"question": "Kuinka paljon sokeria talviruokintaan?",
             "answer": "Noin 15-20 kg sokeria per yhdyskunta talviruokintaan.",
             "confidence": 0.91},
        ]

        engine.train(pairs)
        stats = engine.stats
        assert stats["lookup_count"] >= 1, f"Expected lookups, got {stats['lookup_count']}"
        assert stats["pattern_count"] >= 1, f"Expected patterns, got {stats['pattern_count']}"

        # Verify file was saved
        pf = Path(tmp) / "patterns.json"
        assert pf.exists(), "patterns.json not created"
        data = json.loads(pf.read_text(encoding="utf-8"))
        assert len(data["lookup"]) >= 1
        assert len(data["patterns"]) >= 1


def test_predict_trained_question():
    """V1 predict() returns answer for a trained question."""
    from core.micro_model import PatternMatchEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = PatternMatchEngine(data_dir=tmp, load_configs=False)

        pairs = [
            {"question": "Miten varroapunkkia torjutaan?",
             "answer": "Oksaalihappohoito on tehokas.",
             "confidence": 0.95},
        ]
        engine.train(pairs)

        result = engine.predict("Miten varroapunkkia torjutaan?")
        assert result is not None, "predict() returned None for trained question"
        assert "Oksaalihappo" in result["answer"] or "tehokas" in result["answer"]
        assert result["confidence"] >= 0.85


def test_low_confidence_filtered():
    """Pairs with confidence < 0.90 are filtered out by train()."""
    from core.micro_model import PatternMatchEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = PatternMatchEngine(data_dir=tmp, load_configs=False)

        pairs = [
            {"question": "Huono kysymys?",
             "answer": "Huono vastaus.",
             "confidence": 0.50},
        ]
        engine.train(pairs)
        assert engine.stats["lookup_count"] == 0


def test_persistence_roundtrip():
    """Trained patterns survive save/load cycle."""
    from core.micro_model import PatternMatchEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine1 = PatternMatchEngine(data_dir=tmp, load_configs=False)
        engine1.train([
            {"question": "Mehiläisten talvehtiminen",
             "answer": "Yhdyskunnan tulee olla vahva ja terve ennen talvea.",
             "confidence": 0.93},
        ])

        # Load from same dir
        engine2 = PatternMatchEngine(data_dir=tmp, load_configs=False)
        assert engine2.stats["lookup_count"] >= 1
        result = engine2.predict("Mehiläisten talvehtiminen")
        assert result is not None, "Loaded engine can't predict trained question"


def test_empty_patterns_no_crash():
    """Engine works with empty patterns file."""
    from core.micro_model import PatternMatchEngine

    with tempfile.TemporaryDirectory() as tmp:
        pf = Path(tmp) / "patterns.json"
        pf.write_text('{"lookup":{},"patterns":[],"timestamp":0}', encoding="utf-8")
        engine = PatternMatchEngine(data_dir=tmp, load_configs=False)
        assert engine.predict("anything") is None
        assert engine.stats["lookup_count"] == 0


if __name__ == "__main__":
    tests = [
        test_train_from_pairs,
        test_predict_trained_question,
        test_low_confidence_filtered,
        test_persistence_roundtrip,
        test_empty_patterns_no_crash,
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
