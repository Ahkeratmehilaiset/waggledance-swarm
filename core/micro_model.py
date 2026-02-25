"""
WaggleDance — Phase 10: Micro-Model Training
==============================================
Three progressively capable local models trained on accumulated data.

V1 PatternMatchEngine: regex + lookup table (0.01ms, zero GPU, always works)
V2 ClassifierModel: PyTorch neural net on embeddings (1ms, CPU, requires torch)
V3 LoRAModel: LoRA fine-tuned nano-LLM (50ms, stub/framework)
MicroModelOrchestrator: manages all variants, single predict() interface
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("micro_model")

# ── Finnish stemming suffixes (shared with HotCache in core/fast_memory.py) ──
_FI_SUFFIXES = [
    "kään", "kaan",
    "ssä", "ssa", "stä", "sta", "llä", "lla", "lle", "ltä", "lta",
    "kin", "kö", "ko",
    "an", "en", "in", "on", "un", "yn", "än", "ön",
]


# ═══════════════════════════════════════════════════════════════
# V1: PATTERN MATCH ENGINE (0.01ms, zero GPU)
# ═══════════════════════════════════════════════════════════════

class PatternMatchEngine:
    """Regex + lookup table. Pre-computed Finnish answers for common questions."""

    def __init__(self, data_dir="data/micromodel_v1"):
        self._data_dir = Path(data_dir)
        self._patterns = []  # list of (compiled_regex, answer_fi, confidence)
        self._lookup = {}    # normalized_question → (answer_fi, confidence)
        self._hits = 0
        self._misses = 0
        self._load_patterns()

    def _normalize(self, text: str) -> str:
        """Lowercase, strip punctuation, Finnish stemming normalization.

        Reuses HotCache normalization approach from core/fast_memory.py.
        """
        text = text.lower().strip()
        text = text.rstrip("?!.")
        text = text.strip()
        words = text.split()
        stemmed = []
        for w in words:
            w = re.sub(r'[^\wäöåÄÖÅ]', '', w)
            if not w:
                continue
            for suffix in _FI_SUFFIXES:
                if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                    w = w[:-len(suffix)]
                    break
            if w:
                stemmed.append(w)
        stemmed.sort()
        return " ".join(stemmed)

    def predict(self, question_fi: str) -> Optional[dict]:
        """Try pattern match. Returns {answer, confidence, method} or None.

        1. Exact lookup (normalized)
        2. Regex patterns
        Returns None if no match.
        """
        if not question_fi or not question_fi.strip():
            self._misses += 1
            return None

        # 1. Exact lookup
        key = self._normalize(question_fi)
        if key and key in self._lookup:
            answer, conf = self._lookup[key]
            self._hits += 1
            return {"answer": answer, "confidence": conf, "method": "v1_exact"}

        # 2. Regex patterns
        q_lower = question_fi.lower().strip()
        for pattern, answer, conf in self._patterns:
            if pattern.search(q_lower):
                self._hits += 1
                return {"answer": answer, "confidence": conf, "method": "v1_regex"}

        self._misses += 1
        return None

    def train(self, pairs: list):
        """Build patterns from high-confidence Q&A pairs.

        Only pairs with confidence >= 0.90 and reasonably sized answers.
        Creates exact lookup entries + simple regex patterns.
        Saves to data/micromodel_v1/patterns.json
        """
        self._lookup.clear()
        self._patterns.clear()

        for pair in pairs:
            conf = pair.get("confidence", 0)
            if conf < 0.90:
                continue

            question = pair.get("pattern", pair.get("question", ""))
            answer = pair.get("answer", "")
            if not question or not answer:
                continue

            # Add exact lookup
            key = self._normalize(question)
            if key:
                self._lookup[key] = (answer, conf)

            # Build regex for the question (escape special chars, allow fuzzy)
            try:
                words = re.sub(r'[^\wäöåÄÖÅ\s]', '', question.lower()).split()
                if len(words) >= 2:
                    # Create pattern matching key words in any order
                    key_words = [w for w in words if len(w) > 3][:4]
                    if key_words:
                        # Pattern: all key words must be present
                        regex_parts = [re.escape(w) for w in key_words]
                        regex_str = "(?=.*" + ")(?=.*".join(regex_parts) + ")"
                        compiled = re.compile(regex_str)
                        self._patterns.append((compiled, answer, conf * 0.95))
            except re.error:
                continue

        self._save_patterns()
        log.info(f"V1 trained: {len(self._lookup)} lookups, "
                 f"{len(self._patterns)} patterns")

    def _save_patterns(self):
        """Save patterns to disk."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "lookup": {k: {"answer": v[0], "confidence": v[1]}
                           for k, v in self._lookup.items()},
                "patterns": [{"regex": p.pattern, "answer": a, "confidence": c}
                             for p, a, c in self._patterns],
                "timestamp": time.time(),
            }
            with open(self._data_dir / "patterns.json", "w",
                       encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"V1 save failed: {e}")

    def _load_patterns(self):
        """Load from data/micromodel_v1/patterns.json if exists."""
        path = self._data_dir / "patterns.json"
        if not path.exists():
            return

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            for key, val in data.get("lookup", {}).items():
                self._lookup[key] = (val["answer"], val.get("confidence", 0.90))

            for entry in data.get("patterns", []):
                try:
                    compiled = re.compile(entry["regex"])
                    self._patterns.append(
                        (compiled, entry["answer"], entry.get("confidence", 0.85)))
                except re.error:
                    continue

            log.info(f"V1 loaded: {len(self._lookup)} lookups, "
                     f"{len(self._patterns)} patterns")
        except Exception as e:
            log.warning(f"V1 load failed: {e}")

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "lookup_count": len(self._lookup),
            "pattern_count": len(self._patterns),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(total, 1),
        }


# ═══════════════════════════════════════════════════════════════
# V2: CLASSIFIER MODEL (1ms, CPU only)
# ═══════════════════════════════════════════════════════════════

class ClassifierModel:
    """Small PyTorch neural network trained on nomic-embed vectors.

    Architecture: Linear(768,256) -> ReLU -> Linear(256,128) -> Linear(128,N)
    Input: 768-dim nomic-embed-text vector
    Output: answer class ID -> mapped to pre-stored answer
    Size: ~250K params, ~2MB on disk, <1ms on CPU
    """

    def __init__(self, consciousness=None, model_path="data/micromodel_v2.pt"):
        self.consciousness = consciousness
        self._model = None
        self._answer_map = {}  # class_id (int) → answer text
        self._num_classes = 0
        self._generation = 0
        self._model_path = Path(model_path)
        self._hits = 0
        self._misses = 0
        self._available = False
        self._torch_available = False
        self._check_torch()
        self._load_model()

    def _check_torch(self):
        """Check if PyTorch is installed."""
        try:
            import torch  # noqa: F401
            self._torch_available = True
        except ImportError:
            self._torch_available = False
            log.info("V2: torch not installed, classifier unavailable")

    def _build_model(self, input_dim: int, num_classes: int):
        """Build the neural network."""
        if not self._torch_available:
            return None
        import torch.nn as nn
        model = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, num_classes),
        )
        return model

    def predict(self, question: str, embedding: list = None) -> Optional[dict]:
        """Classify question -> answer class.

        Uses pre-computed embedding if provided, else computes via consciousness.
        Returns {answer, confidence, method, class_id} or None.
        Only returns if softmax confidence > 0.80.
        """
        if not self._available or not self._model or not self._answer_map:
            self._misses += 1
            return None

        if not self._torch_available:
            self._misses += 1
            return None

        import torch
        import torch.nn.functional as F

        # Get embedding
        if embedding is None:
            if (self.consciousness and hasattr(self.consciousness, 'embed')
                    and self.consciousness.embed.available):
                embedding = self.consciousness.embed.embed_query(question)
            if embedding is None:
                self._misses += 1
                return None

        try:
            self._model.eval()
            with torch.no_grad():
                x = torch.tensor([embedding], dtype=torch.float32)
                logits = self._model(x)
                probs = F.softmax(logits, dim=1)
                confidence, class_id = torch.max(probs, dim=1)
                conf = confidence.item()
                cls = class_id.item()

            if conf < 0.80:
                self._misses += 1
                return None

            answer = self._answer_map.get(cls)
            if not answer:
                self._misses += 1
                return None

            self._hits += 1
            return {
                "answer": answer,
                "confidence": conf,
                "method": "v2_classifier",
                "class_id": cls,
            }
        except Exception as e:
            log.warning(f"V2 predict error: {e}")
            self._misses += 1
            return None

    def train(self, training_data: dict, epochs: int = 50, lr: float = 0.001):
        """Train classifier on training data.

        training_data: {questions: list[str], answer_ids: list[int],
                        answers: list[str], embeddings: list[list[float]]}
        """
        if not self._torch_available:
            log.warning("V2: Cannot train — torch not installed")
            return False

        import torch
        import torch.nn as nn
        import torch.optim as optim

        questions = training_data.get("questions", [])
        answer_ids = training_data.get("answer_ids", [])
        answers = training_data.get("answers", [])
        embeddings = training_data.get("embeddings", [])

        if not embeddings or not answer_ids or not answers:
            log.warning("V2: No training data provided")
            return False

        num_classes = len(answers)
        if num_classes < 2:
            log.warning("V2: Need at least 2 classes to train")
            return False

        input_dim = len(embeddings[0])

        # Build model
        self._model = self._build_model(input_dim, num_classes)
        if self._model is None:
            return False

        # Convert to tensors
        X = torch.tensor(embeddings, dtype=torch.float32)
        y = torch.tensor(answer_ids, dtype=torch.long)

        # 80/20 split
        n = len(X)
        n_train = max(int(n * 0.8), 1)
        indices = torch.randperm(n)
        X_train, y_train = X[indices[:n_train]], y[indices[:n_train]]
        X_val, y_val = X[indices[n_train:]], y[indices[n_train:]]

        # Train
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(self._model.parameters(), lr=lr)

        best_val_acc = 0.0
        for epoch in range(epochs):
            self._model.train()
            optimizer.zero_grad()
            outputs = criterion(self._model(X_train), y_train)
            outputs.backward()
            optimizer.step()

            # Validation
            if len(X_val) > 0 and (epoch + 1) % 10 == 0:
                self._model.eval()
                with torch.no_grad():
                    val_preds = self._model(X_val).argmax(dim=1)
                    val_acc = (val_preds == y_val).float().mean().item()
                    if val_acc > best_val_acc:
                        best_val_acc = val_acc

        # Store answer map
        self._answer_map = {i: a for i, a in enumerate(answers)}
        self._num_classes = num_classes
        self._generation += 1
        self._available = True

        # Save model
        self._save_model()

        log.info(f"V2 trained: gen{self._generation}, "
                 f"{num_classes} classes, val_acc={best_val_acc:.0%}")

        # Log training result
        try:
            log_path = Path("data/micromodel_v2_train.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "generation": self._generation,
                    "num_classes": num_classes,
                    "num_samples": n,
                    "epochs": epochs,
                    "best_val_acc": best_val_acc,
                    "timestamp": time.time(),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return True

    def _save_model(self):
        """Save trained model to disk."""
        if not self._torch_available or not self._model:
            return
        try:
            import torch
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "model_state": self._model.state_dict(),
                "answer_map": self._answer_map,
                "num_classes": self._num_classes,
                "generation": self._generation,
                "input_dim": next(self._model.parameters()).shape[1],
            }, self._model_path)
            log.info(f"V2 model saved to {self._model_path}")
        except Exception as e:
            log.warning(f"V2 save failed: {e}")

    def _load_model(self):
        """Load trained model from disk if exists."""
        if not self._torch_available:
            return
        if not self._model_path.exists():
            return

        try:
            import torch
            checkpoint = torch.load(self._model_path,
                                    map_location="cpu",
                                    weights_only=False)
            input_dim = checkpoint.get("input_dim", 768)
            num_classes = checkpoint.get("num_classes", 0)

            if num_classes < 2:
                return

            self._model = self._build_model(input_dim, num_classes)
            self._model.load_state_dict(checkpoint["model_state"])
            self._model.eval()
            self._answer_map = checkpoint.get("answer_map", {})
            # Ensure keys are ints
            self._answer_map = {int(k): v
                                for k, v in self._answer_map.items()}
            self._num_classes = num_classes
            self._generation = checkpoint.get("generation", 1)
            self._available = True
            log.info(f"V2 loaded: gen{self._generation}, "
                     f"{num_classes} classes")
        except Exception as e:
            log.warning(f"V2 load failed: {e}")
            self._available = False

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "available": self._available,
            "torch_available": self._torch_available,
            "generation": self._generation,
            "num_classes": self._num_classes,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(total, 1),
        }


# ═══════════════════════════════════════════════════════════════
# V3: LORA MODEL (stub/framework, 50ms)
# ═══════════════════════════════════════════════════════════════

class LoRAModel:
    """LoRA fine-tuned nano-LLM framework.

    Base: smollm2:135m via Ollama.
    Actual LoRA training requires peft+transformers — this is the framework.
    In Phase 10 we implement the interface and stub the training.
    Full training pipeline deferred to when peft is installed.
    """

    def __init__(self, model_name="smollm2:135m",
                 data_dir="data/lora_adapters"):
        self._generation = 0
        self._available = False
        self._model_name = model_name
        self._data_dir = Path(data_dir)
        self._hits = 0
        self._misses = 0
        self._peft_available = False
        self._check_peft()

    def _check_peft(self):
        """Check if peft+transformers are installed."""
        try:
            import peft  # noqa: F401
            import transformers  # noqa: F401
            self._peft_available = True
        except ImportError:
            self._peft_available = False
            log.info("V3: peft/transformers not installed, "
                     "LoRA training unavailable")

    def predict(self, question: str) -> Optional[dict]:
        """Generate response via Ollama if model available.

        Returns None if model not loaded or not trained.
        """
        if not self._available:
            self._misses += 1
            return None

        # V3 not included in router until generation >= 5
        if self._generation < 5:
            self._misses += 1
            return None

        # Future: call Ollama with LoRA-adapted model
        self._misses += 1
        return None

    def train(self, training_pairs: list):
        """LoRA training stub.

        Logs intent, checks for peft/transformers.
        If libraries available: actual LoRA fine-tuning (future).
        If not: logs warning, returns gracefully.
        """
        if not self._peft_available:
            log.info("V3: LoRA training skipped — peft not installed")
            return False

        # Future: actual LoRA fine-tuning when peft is installed
        log.info(f"V3: LoRA training stub called with "
                 f"{len(training_pairs)} pairs")
        self._generation += 1

        # Save training data for future use
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data_path = self._data_dir / f"training_data_gen{self._generation}.jsonl"
            with open(data_path, "w", encoding="utf-8") as f:
                for pair in training_pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            log.info(f"V3: Saved training data to {data_path}")
        except Exception as e:
            log.warning(f"V3: Failed to save training data: {e}")

        return True

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "available": self._available,
            "peft_available": self._peft_available,
            "generation": self._generation,
            "model_name": self._model_name,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(total, 1),
        }


# ═══════════════════════════════════════════════════════════════
# TOPIC AUTO-PROMOTION (Phase 6: Micro-Model Auto-Promotion)
# ═══════════════════════════════════════════════════════════════

class TopicPromotionManager:
    """Track per-topic stats and auto-promote topics with enough data + low errors.

    Promotion criteria: total_pairs >= min_pairs AND error_rate < max_error
    Promoted topics get forced V1/V2 prediction (skip LLM entirely).
    """

    def __init__(self, data_dir="data", min_pairs=200, max_error=0.03):
        self._data_dir = Path(data_dir)
        self._state_path = self._data_dir / "topic_promotions.json"
        self._log_path = self._data_dir / "micro_model_promotions.jsonl"
        self._min_pairs = min_pairs
        self._max_error = max_error
        # topic -> {total_pairs, errors, promoted, promoted_at}
        self._topics: dict[str, dict] = {}
        self._load_state()

    def _load_state(self):
        if self._state_path.exists():
            try:
                with open(self._state_path, encoding="utf-8") as f:
                    self._topics = json.load(f)
                log.info(f"TopicPromotion: loaded {len(self._topics)} topics, "
                         f"{sum(1 for t in self._topics.values() if t.get('promoted'))} promoted")
            except Exception as e:
                log.warning(f"TopicPromotion load failed: {e}")

    def _save_state(self):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(self._topics, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"TopicPromotion save failed: {e}")

    def record_pair(self, topic: str, was_error: bool = False):
        """Record a training pair for a topic."""
        if not topic:
            return
        topic = topic.lower().strip()
        if topic not in self._topics:
            self._topics[topic] = {
                "total_pairs": 0, "errors": 0,
                "promoted": False, "promoted_at": None,
            }
        self._topics[topic]["total_pairs"] += 1
        if was_error:
            self._topics[topic]["errors"] += 1
        # Check for promotion
        self._check_promotion(topic)

    def _check_promotion(self, topic: str):
        """Check if topic should be promoted."""
        t = self._topics.get(topic)
        if not t or t.get("promoted"):
            return
        if t["total_pairs"] < self._min_pairs:
            return
        error_rate = t["errors"] / max(t["total_pairs"], 1)
        if error_rate < self._max_error:
            t["promoted"] = True
            t["promoted_at"] = time.time()
            self._save_state()
            self._log_promotion(topic, t)
            log.info(f"Topic PROMOTED: '{topic}' "
                     f"({t['total_pairs']} pairs, {error_rate:.1%} errors)")

    def _log_promotion(self, topic: str, state: dict):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "topic": topic,
                    "total_pairs": state["total_pairs"],
                    "errors": state["errors"],
                    "error_rate": state["errors"] / max(state["total_pairs"], 1),
                    "promoted_at": state["promoted_at"],
                    "timestamp": time.time(),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def is_promoted(self, topic: str) -> bool:
        """Check if topic is promoted (should skip LLM)."""
        if not topic:
            return False
        return self._topics.get(topic.lower().strip(), {}).get("promoted", False)

    @property
    def stats(self) -> dict:
        promoted = sum(1 for t in self._topics.values() if t.get("promoted"))
        return {
            "total_topics": len(self._topics),
            "promoted_topics": promoted,
            "min_pairs": self._min_pairs,
            "max_error": self._max_error,
        }


# ═══════════════════════════════════════════════════════════════
# MICRO-MODEL ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class MicroModelOrchestrator:
    """Manages all 3 micro-model variants. Provides single predict() interface.

    Handles training scheduling and model lifecycle.
    """

    TRAINING_INTERVAL_CYCLES = 50  # Train every 50 night cycles

    def __init__(self, consciousness, collector, data_dir="data"):
        self.consciousness = consciousness
        self.collector = collector
        _data = Path(data_dir)
        self.v1 = PatternMatchEngine(data_dir=str(_data / "micromodel_v1"))
        self.v2 = ClassifierModel(consciousness,
                                  model_path=str(_data / "micromodel_v2.pt"))
        self.v3 = LoRAModel(data_dir=str(_data / "lora_adapters"))
        self._training_count = 0
        self._last_train_cycle = 0

        # Phase 6: Topic auto-promotion
        try:
            import yaml as _yaml
            _cfg_path = Path("configs/settings.yaml")
            if _cfg_path.exists():
                with open(_cfg_path, encoding="utf-8") as _f:
                    _cfg = _yaml.safe_load(_f) or {}
                _al = _cfg.get("advanced_learning", {})
                _promo_enabled = _al.get("micro_model_promotion_enabled", True)
                _promo_min = _al.get("micro_model_promotion_min_pairs", 200)
                _promo_max_err = _al.get("micro_model_promotion_max_error", 0.03)
            else:
                _promo_enabled = True
                _promo_min = 200
                _promo_max_err = 0.03
        except Exception:
            _promo_enabled = True
            _promo_min = 200
            _promo_max_err = 0.03

        self.promotions = TopicPromotionManager(
            data_dir=data_dir, min_pairs=_promo_min, max_error=_promo_max_err
        ) if _promo_enabled else None

    def predict(self, question_fi: str, embedding: list = None,
                topic_hint: str = None) -> Optional[dict]:
        """Try models in order: V1 (0.01ms) -> V2 (1ms).

        V3 not included in router until generation >= 5.
        Returns first match above confidence threshold, or None.
        """
        # Phase 6: Force V1/V2 for promoted topics
        if topic_hint and self.promotions and self.promotions.is_promoted(topic_hint):
            result = self.v1.predict(question_fi)
            if result:
                result["promoted"] = True
                return result
            if self.v2._available:
                result = self.v2.predict(question_fi, embedding=embedding)
                if result:
                    result["promoted"] = True
                    return result

        # Try V1 first (fastest)
        result = self.v1.predict(question_fi)
        if result and result.get("confidence", 0) > 0.85:
            return result

        # Try V2 (if available)
        if self.v2._available:
            result = self.v2.predict(question_fi, embedding=embedding)
            if result and result.get("confidence", 0) > 0.80:
                return result

        return None

    def is_training_due(self, night_cycle_count: int) -> bool:
        """True if enough cycles have elapsed since last training."""
        return (night_cycle_count - self._last_train_cycle
                >= self.TRAINING_INTERVAL_CYCLES)

    async def maybe_train(self, night_cycle_count: int, throttle=None):
        """Check if training is due and train if possible.

        1. Collector gathers data from all sources
        2. If enough pairs: train V1 (always), V2 (if torch), V3 (if peft)
        3. Save models to disk
        """
        if not self.is_training_due(night_cycle_count):
            return

        self._last_train_cycle = night_cycle_count
        self._training_count += 1

        log.info(f"🤖 Micro-model training cycle #{self._training_count}")

        # Collect fresh data
        self.collector.reset()
        total = self.collector.collect_all()
        if total == 0:
            log.info("No training data available")
            return

        # Train V1 (always works)
        v1_data = self.collector.export_for_v1()
        if v1_data:
            self.v1.train(v1_data)

        # Train V2 (if torch available)
        if self.v2._torch_available:
            v2_data = self.collector.export_for_v2()
            if v2_data and len(v2_data.get("questions", [])) >= 10:
                # Compute embeddings for questions
                embeddings = self._compute_embeddings(v2_data["questions"])
                if embeddings and len(embeddings) == len(v2_data["questions"]):
                    v2_data["embeddings"] = embeddings
                    self.v2.train(v2_data)

        # Train V3 (stub — saves data for future)
        all_pairs = self.collector.get_training_data(min_pairs=10)
        if all_pairs:
            self.v3.train(all_pairs)

        # Save collector pairs
        self.collector.save_pairs()

        log.info(f"🤖 Training complete: V1={self.v1.stats['lookup_count']} lookups, "
                 f"V2={'available' if self.v2._available else 'unavailable'}, "
                 f"V3=gen{self.v3._generation}")

    def _compute_embeddings(self, questions: list) -> Optional[list]:
        """Compute embeddings for questions via consciousness."""
        if (not self.consciousness
                or not hasattr(self.consciousness, 'embed')
                or not self.consciousness.embed.available):
            return None
        try:
            # Use embed_batch if available
            if hasattr(self.consciousness.embed, 'embed_batch'):
                return self.consciousness.embed.embed_batch(
                    questions, mode="query")
            # Fallback to individual
            embeddings = []
            for q in questions:
                emb = self.consciousness.embed.embed_query(q)
                if emb:
                    embeddings.append(emb)
                else:
                    return None
            return embeddings
        except Exception as e:
            log.warning(f"Embedding computation failed: {e}")
            return None

    @property
    def stats(self) -> dict:
        return {
            "training_count": self._training_count,
            "last_train_cycle": self._last_train_cycle,
            "v1": self.v1.stats,
            "v2": self.v2.stats,
            "v3": self.v3.stats,
            "collector": self.collector.stats,
            "promotions": self.promotions.stats if self.promotions else {},
        }
