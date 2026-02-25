"""
WaggleDance — Phase 10: Training Data Collector
=================================================
Curates high-quality Q&A pairs from multiple data sources for micro-model training.

Sources:
  - data/finetune_live.jsonl (from hivemind._log_finetune)
  - ChromaDB waggle_memory (high-confidence facts)
  - ChromaDB corrections (user corrections = ground truth)
  - Round Table consensus results
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("training_collector")


class TrainingDataCollector:
    """Collect and curate training data for micro-models."""

    CONFIDENCE_THRESHOLDS = {
        "round_table_consensus": 0.90,
        "user_accepted": 0.85,
        "expert_distillation": 0.95,
        "chromadb_high_confidence": 0.80,
        "web_trusted": 0.75,
    }
    MIN_TRAINING_CONFIDENCE = 0.75

    def __init__(self, consciousness, data_dir="data"):
        self.consciousness = consciousness
        self._data_dir = Path(data_dir)
        self._pairs = []  # list of {question, answer, source, confidence, timestamp}
        self._seen_keys = set()  # normalized question keys for dedup
        self._total_collected = 0
        self._total_rejected = 0

    def _normalize_question(self, text: str) -> str:
        """Normalize question text for deduplication."""
        text = text.lower().strip()
        text = re.sub(r'[^\wäöåÄÖÅ\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def collect_training_pair(self, question: str, answer: str,
                              source: str, confidence: float) -> bool:
        """Add a single Q&A pair if it meets quality threshold.

        Returns True if pair was accepted, False if rejected.
        """
        if not question or not answer:
            self._total_rejected += 1
            return False

        if confidence < self.MIN_TRAINING_CONFIDENCE:
            self._total_rejected += 1
            return False

        if len(answer.strip()) < 10:
            self._total_rejected += 1
            return False

        key = self._normalize_question(question)
        if not key:
            self._total_rejected += 1
            return False

        if key in self._seen_keys:
            self._total_rejected += 1
            return False

        self._seen_keys.add(key)
        self._pairs.append({
            "question": question.strip(),
            "answer": answer.strip(),
            "source": source,
            "confidence": confidence,
            "timestamp": time.time(),
        })
        self._total_collected += 1
        return True

    def collect_from_finetune_live(self) -> int:
        """Parse data/finetune_live.jsonl for Q&A pairs.

        Format from hivemind._log_finetune():
        {"messages": [
            {"role":"system","content":...},
            {"role":"user","content":Q},
            {"role":"assistant","content":A}
        ], "agent":..., "timestamp":...}

        Only keeps pairs where response >= 50 chars and not an error.
        """
        finetune_path = self._data_dir / "finetune_live.jsonl"
        if not finetune_path.exists():
            log.info("No finetune_live.jsonl found")
            return 0

        count = 0
        _error_markers = ["error", "virhe", "epäonnistui", "failed",
                          "timeout", "ei saatavilla"]
        try:
            with open(finetune_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    messages = entry.get("messages", [])
                    if not isinstance(messages, list) or len(messages) < 3:
                        continue

                    question = ""
                    answer = ""
                    for msg in messages:
                        role = msg.get("role", "")
                        content = msg.get("content", "")
                        if role == "user" and content:
                            question = content
                        elif role == "assistant" and content:
                            answer = content

                    if not question or not answer:
                        continue
                    if len(answer) < 50:
                        continue

                    # Skip error responses
                    answer_lower = answer.lower()
                    if any(m in answer_lower for m in _error_markers):
                        continue

                    if self.collect_training_pair(
                            question, answer, "finetune_live", 0.80):
                        count += 1
        except Exception as e:
            log.warning(f"Error reading finetune_live.jsonl: {e}")

        log.info(f"Collected {count} pairs from finetune_live.jsonl")
        return count

    def collect_from_chromadb(self, min_confidence: float = 0.80) -> int:
        """Pull high-confidence facts from waggle_memory collection.

        Creates Q&A pairs where question is topic-based and answer is the fact.
        """
        if not self.consciousness or not hasattr(self.consciousness, 'memory'):
            return 0

        memory = self.consciousness.memory
        if not hasattr(memory, 'collection') or memory.count == 0:
            return 0

        count = 0
        try:
            # Get facts with metadata
            batch_size = 100
            total = memory.count
            offset = 0

            while offset < total:
                try:
                    results = memory.collection.get(
                        limit=batch_size,
                        offset=offset,
                        include=["documents", "metadatas"]
                    )
                except Exception:
                    break

                docs = results.get("documents", [])
                metas = results.get("metadatas", [])

                if not docs:
                    break

                for doc, meta in zip(docs, metas or [{}] * len(docs)):
                    if not doc or not isinstance(doc, str):
                        continue

                    conf = float(meta.get("confidence", 0.5)) if meta else 0.5
                    validated = meta.get("validated", False) if meta else False

                    if conf < min_confidence:
                        continue

                    # Generate a question from the fact
                    # Use first few words as topic indicator
                    fact_text = doc.strip()
                    if len(fact_text) < 20:
                        continue

                    # Create a simple question from the fact
                    words = fact_text.split()[:5]
                    topic = " ".join(words)
                    question = f"Kerro {topic}?"

                    source = "chromadb_validated" if validated else "chromadb_high_confidence"
                    effective_conf = min(conf, 0.90) if validated else conf

                    if self.collect_training_pair(
                            question, fact_text, source, effective_conf):
                        count += 1

                offset += batch_size
                if len(docs) < batch_size:
                    break

        except Exception as e:
            log.warning(f"Error collecting from ChromaDB: {e}")

        log.info(f"Collected {count} pairs from ChromaDB")
        return count

    def collect_from_corrections(self) -> int:
        """Pull user corrections (ground truth, confidence=0.95).

        Corrections stored in consciousness.memory.corrections collection.
        """
        if not self.consciousness or not hasattr(self.consciousness, 'memory'):
            return 0

        memory = self.consciousness.memory
        if not hasattr(memory, 'corrections'):
            return 0

        count = 0
        try:
            corr_count = memory.corrections.count()
            if corr_count == 0:
                return 0

            results = memory.corrections.get(
                limit=min(corr_count, 500),
                include=["documents", "metadatas"]
            )

            docs = results.get("documents", [])
            metas = results.get("metadatas", [])

            for doc, meta in zip(docs, metas or [{}] * len(docs)):
                if not doc:
                    continue

                # Corrections format: "Q: ... BAD: ... GOOD: ..."
                # Extract Q and GOOD parts
                text = doc.strip()
                q_match = re.search(r'Q:\s*(.+?)(?:BAD:|$)', text)
                good_match = re.search(r'GOOD:\s*(.+?)$', text)

                if q_match and good_match:
                    question = q_match.group(1).strip()
                    answer = good_match.group(1).strip()
                    if question and answer:
                        if self.collect_training_pair(
                                question, answer, "user_correction", 0.95):
                            count += 1

        except Exception as e:
            log.warning(f"Error collecting from corrections: {e}")

        log.info(f"Collected {count} pairs from corrections")
        return count

    def collect_all(self) -> int:
        """Collect from all sources. Returns total pairs collected."""
        total = 0
        total += self.collect_from_finetune_live()
        total += self.collect_from_chromadb()
        total += self.collect_from_corrections()
        log.info(f"Total collected: {total} pairs "
                 f"(rejected: {self._total_rejected})")
        return total

    def get_training_data(self, min_pairs: int = 100) -> Optional[list]:
        """Return all collected pairs, deduplicated.

        Returns None if < min_pairs available (not enough data to train).
        """
        if len(self._pairs) < min_pairs:
            log.info(f"Not enough training pairs: {len(self._pairs)} < {min_pairs}")
            return None
        return list(self._pairs)

    def export_for_v1(self) -> list:
        """Export for V1 PatternMatchEngine.

        Only high-confidence (>0.90) pairs with short, definitive answers.
        Returns list of {pattern: str, answer: str}.
        """
        v1_pairs = []
        for pair in self._pairs:
            if pair["confidence"] < 0.90:
                continue
            # V1 needs concise answers
            if len(pair["answer"]) > 500:
                continue
            v1_pairs.append({
                "pattern": pair["question"],
                "answer": pair["answer"],
                "confidence": pair["confidence"],
            })
        return v1_pairs

    def export_for_v2(self) -> Optional[dict]:
        """Export for V2 ClassifierModel.

        Groups similar answers into classes.
        Returns {questions: list[str], answer_ids: list[int], answers: list[str]}
        or None if not enough data.
        """
        if len(self._pairs) < 10:
            return None

        # Group answers into classes by normalized text
        answer_classes = {}  # normalized_answer → class_id
        class_answers = []   # class_id → canonical answer text
        next_class_id = 0

        questions = []
        answer_ids = []

        for pair in self._pairs:
            answer_norm = pair["answer"].strip().lower()[:100]
            if answer_norm not in answer_classes:
                answer_classes[answer_norm] = next_class_id
                class_answers.append(pair["answer"])
                next_class_id += 1

            class_id = answer_classes[answer_norm]
            questions.append(pair["question"])
            answer_ids.append(class_id)

        return {
            "questions": questions,
            "answer_ids": answer_ids,
            "answers": class_answers,
        }

    def save_pairs(self):
        """Save curated pairs to data/training_pairs.jsonl."""
        out_path = self._data_dir / "training_pairs.jsonl"
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                for pair in self._pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            log.info(f"Saved {len(self._pairs)} training pairs to {out_path}")
        except Exception as e:
            log.warning(f"Failed to save training pairs: {e}")

    def reset(self):
        """Clear all collected pairs for fresh collection."""
        self._pairs.clear()
        self._seen_keys.clear()
        self._total_collected = 0
        self._total_rejected = 0

    @property
    def stats(self) -> dict:
        sources = {}
        for p in self._pairs:
            src = p.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        return {
            "total_pairs": len(self._pairs),
            "total_collected": self._total_collected,
            "total_rejected": self._total_rejected,
            "sources": sources,
        }
