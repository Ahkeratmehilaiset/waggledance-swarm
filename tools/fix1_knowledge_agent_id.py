#!/usr/bin/env python3
"""Fix 1: Update knowledge/*/core.yaml agent_id to match directory name."""

import os
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

fixed = 0
skipped = 0
errors = []

for d in sorted(os.listdir(str(KNOWLEDGE_DIR))):
    core_path = KNOWLEDGE_DIR / d / "core.yaml"
    if not core_path.exists():
        continue

    try:
        with open(core_path, encoding="utf-8-sig") as f:
            content = f.read()

        # Find current agent_id in header
        match = re.search(r"^(\s*agent_id:\s*)(.+)$", content, re.MULTILINE)
        if not match:
            skipped += 1
            continue

        current_id = match.group(2).strip().strip("'\"")
        if current_id == d:
            skipped += 1
            continue

        # Replace agent_id with directory name
        new_content = content[:match.start()] + match.group(1) + d + content[match.end():]

        with open(core_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)

        print(f"  FIXED: {d}/core.yaml: {current_id} -> {d}")
        fixed += 1

    except Exception as e:
        errors.append(f"{d}: {e}")

print(f"\nResults: {fixed} fixed, {skipped} already correct, {len(errors)} errors")
for e in errors:
    print(f"  ERROR: {e}")
