"""
WaggleDance — Training Data Collection CLI
============================================
Reads data/learning_metrics.jsonl + finetune_live.jsonl and exports
high-quality Q&A pairs for LoRA fine-tuning.

Usage:
    python tools/collect_training_data.py                    # Collect + export
    python tools/collect_training_data.py --min-confidence 0.85
    python tools/collect_training_data.py --output data/training_v3.jsonl
    python tools/collect_training_data.py --stats            # Show stats only
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("collect_training")


def collect_from_finetune_live(data_dir: Path, min_confidence: float = 0.80,
                                min_answer_len: int = 50) -> list:
    """Read finetune_live.jsonl and extract high-quality pairs."""
    path = data_dir / "finetune_live.jsonl"
    if not path.exists():
        log.warning(f"Not found: {path}")
        return []

    error_markers = ["error", "virhe", "epäonnistui", "failed",
                     "timeout", "ei saatavilla"]
    pairs = []
    seen = set()
    skipped = {"short": 0, "error": 0, "dup": 0, "no_qa": 0, "low_quality": 0}

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            messages = entry.get("messages", [])
            if not isinstance(messages, list) or len(messages) < 2:
                skipped["no_qa"] += 1
                continue

            question = ""
            answer = ""
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and content:
                    question = content.strip()
                elif role == "assistant" and content:
                    answer = content.strip()

            if not question or not answer:
                skipped["no_qa"] += 1
                continue

            if len(answer) < min_answer_len:
                skipped["short"] += 1
                continue

            answer_lower = answer.lower()
            if any(m in answer_lower for m in error_markers):
                skipped["error"] += 1
                continue

            # Quality score filter (if present)
            quality = entry.get("quality_score", 10)
            if quality < 7:
                skipped["low_quality"] += 1
                continue

            # Dedup by question
            key = question.lower()[:100]
            if key in seen:
                skipped["dup"] += 1
                continue
            seen.add(key)

            pairs.append({
                "question": question,
                "answer": answer,
                "source": "finetune_live",
                "confidence": min(quality / 10.0, 1.0),
                "agent": entry.get("agent_type", ""),
            })

    log.info(f"finetune_live.jsonl: {len(pairs)} pairs collected, "
             f"skipped: {json.dumps(skipped)}")
    return pairs


def collect_from_curated(data_dir: Path) -> list:
    """Read finetune_curated.jsonl (pre-filtered high-quality)."""
    path = data_dir / "finetune_curated.jsonl"
    if not path.exists():
        return []

    pairs = []
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            messages = entry.get("messages", [])
            question = ""
            answer = ""
            for msg in messages if isinstance(messages, list) else []:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and content:
                    question = content.strip()
                elif role == "assistant" and content:
                    answer = content.strip()

            if not question or not answer or len(answer) < 30:
                continue

            key = question.lower()[:100]
            if key in seen:
                continue
            seen.add(key)

            pairs.append({
                "question": question,
                "answer": answer,
                "source": "finetune_curated",
                "confidence": 0.90,
                "agent": entry.get("agent_type", ""),
            })

    log.info(f"finetune_curated.jsonl: {len(pairs)} pairs collected")
    return pairs


def export_pairs(pairs: list, output_path: Path):
    """Write pairs as JSONL for LoRA training."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Sort by confidence descending
    pairs.sort(key=lambda p: p.get("confidence", 0), reverse=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            # Output in chat format suitable for fine-tuning
            record = {
                "messages": [
                    {"role": "system", "content": "Olet mehiläishoitaja-asiantuntija. Vastaa tarkasti ja tieteellisesti."},
                    {"role": "user", "content": pair["question"]},
                    {"role": "assistant", "content": pair["answer"]},
                ],
                "source": pair.get("source", ""),
                "confidence": pair.get("confidence", 0),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.info(f"Exported {len(pairs)} pairs to {output_path}")


def show_stats(pairs: list):
    """Display collection statistics."""
    if not pairs:
        log.info("No pairs collected.")
        return

    sources = {}
    confidences = []
    for p in pairs:
        src = p.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        confidences.append(p.get("confidence", 0))

    avg_conf = sum(confidences) / len(confidences)
    log.info(f"\n=== Training Data Statistics ===")
    log.info(f"Total pairs: {len(pairs)}")
    log.info(f"Avg confidence: {avg_conf:.3f}")
    log.info(f"Sources:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        log.info(f"  {src}: {count}")
    log.info(f"Target: 500+ pairs {'✓ MET' if len(pairs) >= 500 else '✗ NOT MET'}")


def main():
    parser = argparse.ArgumentParser(
        description="Collect training data for WaggleDance LoRA fine-tuning"
    )
    parser.add_argument("--data-dir", default="data",
                        help="Data directory (default: data)")
    parser.add_argument("--output", default="data/training_v3.jsonl",
                        help="Output JSONL path (default: data/training_v3.jsonl)")
    parser.add_argument("--min-confidence", type=float, default=0.80,
                        help="Minimum confidence threshold (default: 0.80)")
    parser.add_argument("--stats", action="store_true",
                        help="Show statistics only, don't export")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        log.error(f"Data directory not found: {data_dir}")
        sys.exit(1)

    log.info("=== WaggleDance Training Data Collector ===\n")

    # Collect from all sources
    all_pairs = []
    all_pairs.extend(collect_from_finetune_live(data_dir, args.min_confidence))
    all_pairs.extend(collect_from_curated(data_dir))

    # Dedup across sources
    seen = set()
    deduped = []
    for p in all_pairs:
        key = p["question"].lower()[:100]
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    log.info(f"\nTotal unique pairs: {len(deduped)}")
    show_stats(deduped)

    if not args.stats:
        output_path = Path(args.output)
        export_pairs(deduped, output_path)


if __name__ == "__main__":
    main()
