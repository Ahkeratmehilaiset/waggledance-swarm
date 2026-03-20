"""Tests for real LoRA V3 training pipeline."""
import pytest
import tempfile
from pathlib import Path


class TestLoRAModelInit:
    def test_predict_returns_none_without_model(self):
        from core.micro_model import LoRAModel
        model = LoRAModel()
        model._available = False
        result = model.predict("test question")
        assert result is None

    def test_predict_returns_none_low_generation(self):
        from core.micro_model import LoRAModel
        model = LoRAModel()
        model._available = True
        model._generation = 1  # < 3
        result = model.predict("test question")
        assert result is None

    def test_train_saves_data_without_peft(self):
        from core.micro_model import LoRAModel
        with tempfile.TemporaryDirectory() as td:
            model = LoRAModel(data_dir=td)
            model._peft_available = False
            pairs = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(60)]
            result = model.train(pairs)
            assert result is False  # No peft → saves data only

    def test_train_requires_50_pairs(self):
        from core.micro_model import LoRAModel
        model = LoRAModel()
        model._peft_available = True
        # Not enough pairs
        with tempfile.TemporaryDirectory() as td:
            model._data_dir = Path(td)
            result = model.train([{"question": "q", "answer": "a"}] * 10)
            assert result is False

    def test_stats_reports_status(self):
        from core.micro_model import LoRAModel
        model = LoRAModel()
        stats = model.stats
        assert "implementation_status" in stats
        assert "available" in stats
        assert "generation" in stats
        assert "peft_available" in stats
        assert stats["implementation_status"] in ("stub_only", "ready")

    def test_save_training_data(self):
        from core.micro_model import LoRAModel
        with tempfile.TemporaryDirectory() as td:
            model = LoRAModel(data_dir=td)
            pairs = [{"question": "q1", "answer": "a1"}]
            model._save_training_data(pairs)
            files = list(Path(td).glob("*.jsonl"))
            assert len(files) == 1
