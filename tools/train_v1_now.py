#!/usr/bin/env python3
"""One-off V1 PatternMatchEngine training from finetune_curated.jsonl.

Reads curated Q/A pairs, filters by quality, strips system prompt boilerplate,
and trains the V1 lookup+regex engine.
"""

import json
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURATED_PATH = ROOT / "data" / "finetune_curated.jsonl"
BOILERPLATE_MARKERS = [
    "OLETUKSET JA KONTEKSTI",
    "ASSUMPTIONS AND CONTEXT",
]


def load_curated_pairs(min_score: float = 8.0, max_answer_len: int = 500):
    """Load Q/A pairs from finetune_curated.jsonl."""
    if not CURATED_PATH.exists():
        print(f"ERROR: {CURATED_PATH} not found")
        return []

    pairs = []
    skipped_score = 0
    skipped_len = 0
    skipped_empty = 0

    with open(CURATED_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            score = entry.get("quality_score", 0)
            if score < min_score:
                skipped_score += 1
                continue

            messages = entry.get("messages", [])
            # Extract user question and assistant answer (skip system prompt)
            question = ""
            answer = ""
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    question = content.strip()
                elif role == "assistant":
                    answer = content.strip()

            if not question or not answer:
                skipped_empty += 1
                continue

            if len(answer) > max_answer_len:
                skipped_len += 1
                continue

            # Strip boilerplate from answer if present
            for marker in BOILERPLATE_MARKERS:
                if marker in answer:
                    # Try to remove the boilerplate section
                    idx = answer.find(marker)
                    if idx > 0:
                        answer = answer[:idx].strip()
                    elif idx == 0:
                        # Entire answer is boilerplate
                        answer = ""
                        break

            if not answer or len(answer) < 10:
                skipped_empty += 1
                continue

            pairs.append({
                "question": question,
                "answer": answer,
                "confidence": min(score / 10.0, 0.99),
            })

    print(f"Loaded {len(pairs)} pairs (skipped: "
          f"score<{min_score}={skipped_score}, "
          f"len>{max_answer_len}={skipped_len}, "
          f"empty={skipped_empty})")
    return pairs


def main():
    print("=" * 60)
    print("V1 PatternMatchEngine — One-off Training")
    print("=" * 60)

    pairs = load_curated_pairs()
    if not pairs:
        print("No pairs to train on. Exiting.")
        return

    # Deduplicate by question
    seen_q = set()
    unique_pairs = []
    for p in pairs:
        q_key = p["question"].lower().strip()
        if q_key not in seen_q:
            seen_q.add(q_key)
            unique_pairs.append(p)
    print(f"After dedup: {len(unique_pairs)} unique pairs")

    from core.micro_model import PatternMatchEngine
    engine = PatternMatchEngine(load_configs=False)

    # Train with all pairs (train() filters by confidence >= 0.90)
    engine.train(unique_pairs)

    stats = engine.stats
    print(f"\nResults:")
    print(f"  Lookups:  {stats['lookup_count']}")
    print(f"  Patterns: {stats['pattern_count']}")
    print(f"  Promoted: {stats['promoted_count']}")

    # Show a few examples
    print(f"\nSample lookups (first 5):")
    for i, (key, (ans, conf)) in enumerate(list(engine._lookup.items())[:5]):
        print(f"  [{conf:.2f}] {key[:50]} -> {ans[:60]}")

    # Test prediction
    if unique_pairs:
        test_q = unique_pairs[0]["question"]
        result = engine.predict(test_q)
        if result:
            print(f"\nTest predict('{test_q[:50]}'):")
            print(f"  -> {result['answer'][:80]} (conf={result['confidence']:.2f})")
        else:
            print(f"\nTest predict('{test_q[:50]}'): no match")

    print(f"\nDone. Patterns saved to data/micromodel_v1/patterns.json")


if __name__ == "__main__":
    main()
