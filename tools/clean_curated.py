#!/usr/bin/env python3
"""One-off cleanup of finetune_curated.jsonl.

- Removes exact hash duplicates (by user+assistant content)
- Strips boilerplate system prompts
- Reports statistics
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURATED_PATH = ROOT / "data" / "finetune_curated.jsonl"
BOILERPLATE_MARKERS = [
    "OLETUKSET JA KONTEKSTI",
    "ASSUMPTIONS AND CONTEXT",
]


def main():
    if not CURATED_PATH.exists():
        print(f"ERROR: {CURATED_PATH} not found")
        return

    entries = []
    with open(CURATED_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    total_before = len(entries)
    print(f"Loaded {total_before} entries")

    # 1. Strip boilerplate system prompts
    boilerplate_stripped = 0
    for entry in entries:
        messages = entry.get("messages", [])
        new_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if any(marker in content for marker in BOILERPLATE_MARKERS):
                    boilerplate_stripped += 1
                    continue  # Skip this system message
            new_messages.append(msg)
        entry["messages"] = new_messages

    print(f"Stripped {boilerplate_stripped} boilerplate system messages")

    # 2. Deduplicate by user+assistant content hash
    seen_hashes = set()
    unique_entries = []
    dupes_removed = 0
    for entry in entries:
        messages = entry.get("messages", [])
        # Build hash from user+assistant content
        key_parts = []
        for msg in messages:
            if msg.get("role") in ("user", "assistant"):
                key_parts.append(msg.get("content", "").lower().strip())
        content_hash = hashlib.md5("|".join(key_parts).encode()).hexdigest()
        if content_hash in seen_hashes:
            dupes_removed += 1
            continue
        seen_hashes.add(content_hash)
        unique_entries.append(entry)

    print(f"Removed {dupes_removed} duplicates")
    print(f"Result: {len(unique_entries)} entries (was {total_before})")

    # 3. Write back
    backup_path = CURATED_PATH.with_suffix(".jsonl.bak")
    CURATED_PATH.rename(backup_path)
    print(f"Backup: {backup_path}")

    with open(CURATED_PATH, "w", encoding="utf-8") as f:
        for entry in unique_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Written: {CURATED_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
