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

# ═══════════════════════════════════════════════════════════════
# V1: PATTERN MATCH ENGINE (0.01ms, zero GPU)
# ═══════════════════════════════════════════════════════════════

_CONFIGS_V1_PATH = Path(__file__).resolve().parent.parent / "configs" / "micro_v1_patterns.json"


class PatternMatchEngine:
    """Regex + lookup table. Pre-computed Finnish answers for common questions."""

    def __init__(self, data_dir="data/micromodel_v1", load_configs=True):
        self._data_dir = Path(data_dir)
        self._patterns = []  # list of (compiled_regex, answer_fi, confidence)
        self._lookup = {}    # normalized_question → (answer_fi, confidence)
        self._hits = 0
        self._misses = 0
        self._answer_tracker: dict[str, dict] = {}  # normalized_q → {answer, count, total}
        self._load_patterns()
        if load_configs:
            self._load_configs_patterns()

    def _normalize(self, text: str) -> str:
        """Normalize Finnish text using Voikko lemmatization.

        Delegates to core.normalizer.normalize_fi() for accurate Finnish
        lemmatization, bee_terms compound expansion, and stopword removal.
        Words sorted alphabetically for order-independent matching.
        """
        from core.normalizer import normalize_fi
        return normalize_fi(text, sort_words=True)

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

    def _load_configs_patterns(self):
        """Load additional patterns from configs/micro_v1_patterns.json.

        These are auto-promoted patterns from consistent answer tracking.
        Merged into the main lookup (does not overwrite training patterns).
        """
        if not _CONFIGS_V1_PATH.exists():
            return
        try:
            with open(_CONFIGS_V1_PATH, encoding="utf-8") as f:
                data = json.load(f)
            count = 0
            for key, val in data.get("promoted", {}).items():
                if key not in self._lookup:  # Don't overwrite training data
                    self._lookup[key] = (val["answer"], val.get("confidence", 0.92))
                    count += 1
            if count:
                log.info(f"V1 configs: loaded {count} promoted patterns")
        except Exception as e:
            log.warning(f"V1 configs load failed: {e}")

    def track_answer(self, question_fi: str, answer_fi: str):
        """Track a consistent answer for potential V1 promotion.

        When the same normalized question gets the same answer >=50 times,
        it's promoted to configs/micro_v1_patterns.json for instant recall.
        """
        if not question_fi or not answer_fi:
            return
        key = self._normalize(question_fi)
        if not key:
            return

        if key not in self._answer_tracker:
            self._answer_tracker[key] = {"answer": answer_fi, "count": 0, "total": 0}

        entry = self._answer_tracker[key]
        entry["total"] += 1
        if entry["answer"] == answer_fi:
            entry["count"] += 1
        else:
            # Different answer — track the more common one
            if entry["count"] < entry["total"] // 2:
                entry["answer"] = answer_fi
                entry["count"] = 1

        # Auto-promote at >=50 consistent answers
        if entry["count"] >= 50 and key not in self._lookup:
            self._promote_pattern(key, answer_fi)

    def _promote_pattern(self, normalized_key: str, answer_fi: str):
        """Promote a consistent pattern to configs/micro_v1_patterns.json."""
        # Add to in-memory lookup
        self._lookup[normalized_key] = (answer_fi, 0.92)

        # Persist to configs file
        try:
            data = {"promoted": {}, "timestamp": time.time()}
            if _CONFIGS_V1_PATH.exists():
                with open(_CONFIGS_V1_PATH, encoding="utf-8") as f:
                    data = json.load(f)

            if "promoted" not in data:
                data["promoted"] = {}
            data["promoted"][normalized_key] = {
                "answer": answer_fi,
                "confidence": 0.92,
                "promoted_at": time.time(),
            }
            data["timestamp"] = time.time()

            _CONFIGS_V1_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CONFIGS_V1_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if _CONFIGS_V1_PATH.exists():
                _CONFIGS_V1_PATH.unlink()
            tmp.rename(_CONFIGS_V1_PATH)

            log.info(f"V1 promoted: '{normalized_key[:40]}' → configs/micro_v1_patterns.json")
        except Exception as e:
            log.warning(f"V1 promote save failed: {e}")

    @property
    def promoted_count(self) -> int:
        """Number of auto-promoted patterns in configs."""
        try:
            if _CONFIGS_V1_PATH.exists():
                with open(_CONFIGS_V1_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                return len(data.get("promoted", {}))
        except Exception:
            pass
        return 0

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "lookup_count": len(self._lookup),
            "pattern_count": len(self._patterns),
            "promoted_count": self.promoted_count,
            "tracked_questions": len(self._answer_tracker),
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
        self._holdout_ratio = 0.2  # v1.16.0: deterministic holdout
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

        # Deterministic holdout (v1.16.0)
        n = len(X)
        holdout_ratio = self._holdout_ratio
        gen = torch.Generator().manual_seed(42 + self._generation)
        indices = torch.randperm(n, generator=gen)
        n_train = max(int(n * (1 - holdout_ratio)), 1)
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

        # v1.16.0: Return structured eval result
        return self._evaluate_holdout(X_val, y_val, answers)

    def _evaluate_holdout(self, X_val, y_val, answers):
        """Evaluate model on holdout set. Returns structured dict or True if no holdout."""
        if not self._torch_available or self._model is None:
            return True
        import torch
        if len(X_val) == 0:
            return True
        try:
            self._model.eval()
            with torch.no_grad():
                val_preds = self._model(X_val).argmax(dim=1)
                correct = (val_preds == y_val).float()
                accuracy = correct.mean().item()
                # Per-class accuracy
                per_class = {}
                for cls_id in range(len(answers)):
                    mask = (y_val == cls_id)
                    if mask.sum().item() > 0:
                        cls_acc = correct[mask].mean().item()
                        per_class[answers[cls_id]] = round(cls_acc, 3)
            return {
                "accuracy": round(accuracy, 4),
                "holdout_size": len(X_val),
                "generation": self._generation,
                "per_class": per_class,
            }
        except Exception as e:
            log.warning(f"V2 eval failed: {e}")
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
    """LoRA fine-tuned nano-LLM (V3 MicroModel).

    Uses PEFT QLoRA to fine-tune a small language model on accumulated
    Q&A data. Training runs overnight on CPU/GPU.

    Base model: loaded from HuggingFace or local cache.
    Training: QLoRA 4-bit quantization, rank=8, alpha=16.
    Inference: merged adapter weights, ~50ms on CPU.
    """

    def __init__(self, model_name="HuggingFaceTB/SmolLM2-135M-Instruct",
                 data_dir="data/lora_adapters",
                 adapter_dir="data/lora_merged"):
        self._generation = 0
        self._available = False
        self._model_name = model_name
        self._data_dir = Path(data_dir)
        self._adapter_dir = Path(adapter_dir)
        self._hits = 0
        self._misses = 0
        self._peft_available = False
        self._model = None
        self._tokenizer = None
        self._implementation_status = "stub_only"
        self._check_peft()
        self._load_merged_model()

    def _check_peft(self):
        """Check if peft + transformers + bitsandbytes are installed."""
        try:
            import peft  # noqa: F401
            import transformers  # noqa: F401
            self._peft_available = True
            self._implementation_status = "ready"
            log.info("V3 LoRA: peft available — real training enabled")
        except ImportError:
            self._peft_available = False
            self._implementation_status = "stub_only"
            log.info("V3: peft not installed, LoRA training unavailable")

    def _load_merged_model(self):
        """Load previously trained and merged model if exists."""
        if not self._peft_available:
            return
        merged_path = self._adapter_dir / "merged"
        if not merged_path.exists():
            return
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(str(merged_path))
            self._model = AutoModelForCausalLM.from_pretrained(
                str(merged_path), device_map="cpu")
            self._model.eval()
            self._available = True
            log.info("V3 LoRA: loaded merged model from %s", merged_path)
        except Exception as e:
            log.warning("V3 LoRA: failed to load merged model: %s", e)
            self._available = False

    def predict(self, question: str) -> Optional[dict]:
        """Generate response using fine-tuned model.

        Returns None if model not available or confidence too low.
        """
        if not self._available or self._model is None or self._tokenizer is None:
            self._misses += 1
            return None

        if self._generation < 3:
            self._misses += 1
            return None

        try:
            import torch
            prompt = f"### Question:\n{question}\n\n### Answer:\n"
            inputs = self._tokenizer(prompt, return_tensors="pt",
                                     max_length=256, truncation=True)
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs, max_new_tokens=128,
                    temperature=0.3, do_sample=True,
                    pad_token_id=self._tokenizer.eos_token_id)

            answer = self._tokenizer.decode(
                outputs[0][inputs["input_ids"].shape[1]:],
                skip_special_tokens=True).strip()

            if not answer or len(answer) < 5:
                self._misses += 1
                return None

            self._hits += 1
            return {
                "answer": answer,
                "confidence": 0.65,
                "method": "v3_lora",
                "generation": self._generation,
            }
        except Exception as e:
            log.warning("V3 predict error: %s", e)
            self._misses += 1
            return None

    def train(self, training_pairs: list):
        """Fine-tune base model with QLoRA on training data.

        1. Load base model with 4-bit quantization
        2. Apply LoRA adapters (rank=8, alpha=16)
        3. Train on Q&A pairs (3 epochs, lr=2e-4)
        4. Merge adapters into base model
        5. Save merged model for inference
        """
        if not self._peft_available:
            log.info("V3: peft not installed, saving data only")
            self._save_training_data(training_pairs)
            return False

        if len(training_pairs) < 50:
            log.info("V3: insufficient data (%d pairs, need 50+)", len(training_pairs))
            self._save_training_data(training_pairs)
            return False

        try:
            import torch
            from transformers import (AutoModelForCausalLM, AutoTokenizer,
                                      TrainingArguments, Trainer)
            from peft import LoraConfig, get_peft_model, TaskType

            log.info("V3 LoRA: starting training with %d pairs", len(training_pairs))

            # 1. Load base model
            tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            # Try 4-bit quantization, fall back to fp32
            try:
                from transformers import BitsAndBytesConfig
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4")
                model = AutoModelForCausalLM.from_pretrained(
                    self._model_name, quantization_config=bnb_config,
                    device_map="auto")
                log.info("V3: loaded base model with 4-bit quantization")
            except Exception:
                model = AutoModelForCausalLM.from_pretrained(
                    self._model_name, device_map="cpu")
                log.info("V3: loaded base model in fp32 (no quantization)")

            # 2. Apply LoRA
            lora_config = LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=8, lora_alpha=16, lora_dropout=0.05,
                target_modules=["q_proj", "v_proj"],
                bias="none")
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()

            # 3. Prepare dataset
            from torch.utils.data import Dataset

            class QADataset(Dataset):
                def __init__(self, pairs, tok, max_length=256):
                    self.encodings = []
                    for pair in pairs:
                        q = pair.get("question", pair.get("pattern", ""))
                        a = pair.get("answer", "")
                        text = f"### Question:\n{q}\n\n### Answer:\n{a}"
                        enc = tok(text, max_length=max_length,
                                  truncation=True, padding="max_length",
                                  return_tensors="pt")
                        enc["labels"] = enc["input_ids"].clone()
                        self.encodings.append({k: v.squeeze(0) for k, v in enc.items()})

                def __len__(self):
                    return len(self.encodings)

                def __getitem__(self, idx):
                    return self.encodings[idx]

            dataset = QADataset(training_pairs, tokenizer)

            # 4. Train
            output_dir = str(self._data_dir / f"lora_gen{self._generation + 1}")
            training_args = TrainingArguments(
                output_dir=output_dir,
                num_train_epochs=3,
                per_device_train_batch_size=4,
                learning_rate=2e-4,
                warmup_steps=10,
                logging_steps=10,
                save_strategy="no",
                report_to="none",
                fp16=torch.cuda.is_available(),
                dataloader_pin_memory=False)

            trainer = Trainer(
                model=model, args=training_args,
                train_dataset=dataset)
            trainer.train()

            # 5. Merge and save
            merged_path = self._adapter_dir / "merged"
            merged_path.mkdir(parents=True, exist_ok=True)
            merged_model = model.merge_and_unload()
            merged_model.save_pretrained(str(merged_path))
            tokenizer.save_pretrained(str(merged_path))

            self._generation += 1
            self._model = merged_model
            self._tokenizer = tokenizer
            self._model.eval()
            self._available = True

            log.info("V3 LoRA: training complete, gen=%d, saved to %s",
                     self._generation, merged_path)
            return True

        except Exception as e:
            log.error("V3 LoRA training failed: %s", e)
            self._save_training_data(training_pairs)
            return False

    def _save_training_data(self, training_pairs: list):
        """Archive training data for future use."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            path = self._data_dir / f"training_data_gen{self._generation + 1}.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                for pair in training_pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("V3: failed to save training data: %s", e)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "available": self._available,
            "peft_available": self._peft_available,
            "implementation_status": self._implementation_status,
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

    def __init__(self, consciousness, collector, data_dir="data", load_configs=True):
        self.consciousness = consciousness
        self.collector = collector
        _data = Path(data_dir)
        self.v1 = PatternMatchEngine(data_dir=str(_data / "micromodel_v1"),
                                     load_configs=load_configs)
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

        # v1.16.0: Eval gate config
        self._min_eval_accuracy = _al.get("micro_model_min_accuracy", 0.70)
        self._min_eval_examples = _al.get("micro_model_min_eval_examples", 25)
        self._eval_enabled = _al.get("micro_model_eval_enabled", True)

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

        # Train V2 (if torch available) — v1.16.0: eval gate
        if self.v2._torch_available:
            v2_data = self.collector.export_for_v2()
            if v2_data and len(v2_data.get("questions", [])) >= 10:
                # Compute embeddings for questions
                embeddings = self._compute_embeddings(v2_data["questions"])
                if embeddings and len(embeddings) == len(v2_data["questions"]):
                    v2_data["embeddings"] = embeddings
                    eval_result = self.v2.train(v2_data)

                    # v1.16.0: Eval gate — check holdout accuracy before promotion
                    if isinstance(eval_result, dict) and self._eval_enabled:
                        self._save_eval_report(eval_result)
                        hs = eval_result.get("holdout_size", 0)
                        acc = eval_result.get("accuracy", 0.0)
                        if (hs >= self._min_eval_examples
                                and acc < self._min_eval_accuracy):
                            self.v2._available = False
                            log.warning(
                                f"V2 eval gate BLOCKED: accuracy={acc:.2%} "
                                f"< {self._min_eval_accuracy:.0%} "
                                f"(holdout={hs})")
                        else:
                            log.info(f"V2 eval gate passed: accuracy={acc:.2%}, "
                                     f"holdout={hs}")

        # V3: Real LoRA training (if peft available + enough data)
        if self.v3._peft_available:
            all_pairs = self.collector.get_training_data(min_pairs=50)
            if all_pairs and len(all_pairs) >= 50:
                log.info("V3 LoRA: starting real training with %d pairs", len(all_pairs))
                self.v3.train(all_pairs)
            else:
                log.debug("V3 LoRA: %d pairs (need 50+), skipping",
                          len(all_pairs) if all_pairs else 0)
        else:
            log.debug("V3 LoRA: peft not installed, skipping")

        # Save collector pairs
        self.collector.save_pairs()

        # v1.16.0: Save active manifest
        self._save_active_manifest()

        log.info(f"Training complete: V1={self.v1.stats['lookup_count']} lookups, "
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

    def _save_eval_report(self, result: dict):
        """Write eval report to data/micro_model_reports/."""
        try:
            report_dir = Path("data/micro_model_reports")
            report_dir.mkdir(parents=True, exist_ok=True)
            gen = result.get("generation", 0)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = report_dir / f"eval_gen{gen}_{ts}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Failed to save eval report: {e}")

    def _save_active_manifest(self):
        """Write data/micro_model_active.json with v1/v2/v3 status."""
        try:
            manifest = {
                "v1": {
                    "available": True,
                    "lookup_count": self.v1.stats.get("lookup_count", 0),
                    "pattern_count": self.v1.stats.get("pattern_count", 0),
                },
                "v2": {
                    "available": self.v2._available,
                    "generation": self.v2._generation,
                    "torch_available": self.v2._torch_available,
                },
                "v3": {
                    "available": self.v3._available,
                    "generation": self.v3._generation,
                    "peft_available": self.v3._peft_available,
                    "implementation_status": self.v3.stats.get(
                        "implementation_status", "stub"),
                },
                "training_count": self._training_count,
                "timestamp": time.time(),
            }
            path = Path("data/micro_model_active.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except Exception as e:
            log.warning(f"Failed to save active manifest: {e}")

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

    # ── v2.0: Specialist model base ─────────────────────────────

    def get_specialist_trainer(self):
        """Return a SpecialistTrainer that can use V2/V3 as training base.

        Bridges the legacy micro-model pipeline to the new specialist
        model training system. The specialist trainer uses case trajectories
        instead of Q&A pairs.
        """
        try:
            from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer
            return SpecialistTrainer(profile=getattr(self, '_profile', 'DEFAULT'))
        except ImportError:
            return None

    def feed_specialist_training(self, case_trajectories: list) -> int:
        """Feed case trajectories into V2/V3 model training pipeline.

        Returns number of features extracted for training.
        """
        trainer = self.get_specialist_trainer()
        if trainer is None:
            return 0
        results = []
        for model_name in ("route_classifier", "intent_classifier"):
            try:
                result = trainer.train(model_name, case_trajectories)
                results.append(result)
            except Exception:
                pass
        return len(results)
