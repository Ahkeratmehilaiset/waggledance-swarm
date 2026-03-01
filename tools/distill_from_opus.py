"""
WaggleDance — Knowledge Distillation Prep (Phase 4g)

Collects failed queries (confidence='none') for expert answers.
Pipeline: collect → format → (external expert) → import

Usage:
  python tools/distill_from_opus.py --collect     Collect failed queries
  python tools/distill_from_opus.py --format      Format as expert prompts
  python tools/distill_from_opus.py --import FILE Import expert answers

The actual API call to an expert model (Claude/GPT) is left as a placeholder.
The import step loads pre-generated expert answers into ChromaDB.
"""

import argparse
import json
import sys
import time
from pathlib import Path


FAILED_QUERIES_PATH = Path("data/failed_queries.jsonl")
PROMPTS_PATH = Path("data/distill_prompts.jsonl")
DEFAULT_ANSWERS_PATH = Path("data/expert_answers.jsonl")


def collect_failed_queries(data_dir="data"):
    """Scan learning_metrics.jsonl and finetune_live.jsonl for low-confidence queries.

    Also checks consciousness stats via ChromaDB if available.
    Writes results to data/failed_queries.jsonl (append, dedup by query text).
    """
    data_path = Path(data_dir)
    existing = set()

    # Load existing failed queries to avoid duplicates
    if FAILED_QUERIES_PATH.exists():
        with open(FAILED_QUERIES_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    existing.add(entry.get("query", ""))
                except Exception:
                    continue

    new_queries = []

    # Source 1: learning_metrics.jsonl — look for low confidence entries
    metrics_path = data_path / "learning_metrics.jsonl"
    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("confidence", 1.0) < 0.3:
                        query = entry.get("query", entry.get("prompt", ""))
                        if query and query not in existing:
                            new_queries.append({
                                "query": query,
                                "source": "learning_metrics",
                                "confidence": entry.get("confidence", 0),
                                "timestamp": entry.get("timestamp",
                                                        time.strftime("%Y-%m-%dT%H:%M:%S")),
                            })
                            existing.add(query)
                except Exception:
                    continue

    # Source 2: finetune_live.jsonl — look for short/bad responses
    finetune_path = data_path / "finetune_live.jsonl"
    if finetune_path.exists():
        with open(finetune_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    messages = entry.get("messages", [])
                    user_msg = ""
                    assistant_msg = ""
                    for m in messages:
                        if m.get("role") == "user":
                            user_msg = m.get("content", "")
                        if m.get("role") == "assistant":
                            assistant_msg = m.get("content", "")
                    # Flag if response is very short or contains error markers
                    if (user_msg and (len(assistant_msg) < 20
                            or "en tiedä" in assistant_msg.lower()
                            or "i don't know" in assistant_msg.lower())):
                        if user_msg not in existing:
                            new_queries.append({
                                "query": user_msg,
                                "source": "finetune_live",
                                "bad_response": assistant_msg[:200],
                                "timestamp": entry.get("timestamp",
                                                        time.strftime("%Y-%m-%dT%H:%M:%S")),
                            })
                            existing.add(user_msg)
                except Exception:
                    continue

    # Write new queries
    if new_queries:
        FAILED_QUERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILED_QUERIES_PATH, "a", encoding="utf-8") as f:
            for q in new_queries:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(f"Collected {len(new_queries)} new failed queries "
          f"(total: {len(existing)})")
    return new_queries


def format_prompts(queries=None):
    """Format failed queries as prompts for expert answers.

    Reads from data/failed_queries.jsonl if queries not provided.
    Writes to data/distill_prompts.jsonl.
    """
    if queries is None:
        if not FAILED_QUERIES_PATH.exists():
            print("No failed queries found. Run --collect first.")
            return []
        queries = []
        with open(FAILED_QUERIES_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    queries.append(json.loads(line.strip()))
                except Exception:
                    continue

    prompts = []
    for q in queries:
        query = q.get("query", "")
        if not query:
            continue
        prompt = {
            "query": query,
            "system": ("You are a Finnish beekeeping expert with 30+ years experience. "
                       "Answer factually and concisely in English. "
                       "Focus on practical Finnish beekeeping conditions. "
                       "2-3 sentences maximum."),
            "user": f"Question from a beekeeper: {query}",
            "metadata": {
                "source": q.get("source", "unknown"),
                "original_timestamp": q.get("timestamp", ""),
            }
        }
        prompts.append(prompt)

    PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROMPTS_PATH, "w", encoding="utf-8") as f:
        for p in prompts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"Formatted {len(prompts)} prompts → {PROMPTS_PATH}")
    return prompts


def import_expert_answers(answers_file=None, consciousness=None):
    """Import expert answers into ChromaDB with confidence=0.95.

    answers_file: Path to JSONL with {"query": ..., "answer": ...} entries.
    consciousness: Consciousness instance (if None, creates one).
    """
    answers_path = Path(answers_file) if answers_file else DEFAULT_ANSWERS_PATH
    if not answers_path.exists():
        print(f"Answers file not found: {answers_path}")
        print("Expected JSONL format: {\"query\": \"...\", \"answer\": \"...\"}")
        return 0

    if consciousness is None:
        try:
            from consciousness import Consciousness
            consciousness = Consciousness(db_path="data/chroma_db")
        except Exception as e:
            print(f"Could not init Consciousness: {e}")
            return 0

    count = 0
    with open(answers_path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                query = entry.get("query", "")
                answer = entry.get("answer", "")
                if not query or not answer:
                    continue
                fact = f"Q: {query} → A: {answer}"
                stored = consciousness.learn(
                    fact, agent_id="expert_distill",
                    source_type="expert_distillation",
                    confidence=0.95, validated=True,
                    immediate=True)
                if stored:
                    count += 1
            except Exception as e:
                print(f"  Error importing: {e}")
                continue

    # Flush any remaining
    consciousness.flush()
    print(f"Imported {count} expert answers into ChromaDB")
    return count


def main():
    parser = argparse.ArgumentParser(
        description="WaggleDance Knowledge Distillation Prep")
    parser.add_argument("--collect", action="store_true",
                        help="Collect failed queries from data files")
    parser.add_argument("--format", action="store_true",
                        help="Format failed queries as expert prompts")
    parser.add_argument("--import-file", type=str, default=None,
                        help="Import expert answers from JSONL file")
    parser.add_argument("--data-dir", type=str, default="data",
                        help="Data directory (default: data)")
    args = parser.parse_args()

    if not any([args.collect, args.format, args.import_file]):
        parser.print_help()
        print("\nExample workflow:")
        print("  1. python tools/distill_from_opus.py --collect")
        print("  2. python tools/distill_from_opus.py --format")
        print("  3. (Send data/distill_prompts.jsonl to expert API)")
        print("  4. python tools/distill_from_opus.py --import-file data/expert_answers.jsonl")
        return

    if args.collect:
        collect_failed_queries(args.data_dir)

    if args.format:
        format_prompts()

    if args.import_file:
        import_expert_answers(args.import_file)


if __name__ == "__main__":
    main()
