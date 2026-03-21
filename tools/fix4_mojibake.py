#!/usr/bin/env python3
"""Fix 4: Fix BOM + double-encoded UTF-8 (mojibake) in YAML files.

Handles both latin-1 and Windows-1252 double-encoding patterns.
Programmatically generates replacement map for all Finnish chars + symbols.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent

SCAN_DIRS = [
    PROJECT_DIR / "agents",
    PROJECT_DIR / "knowledge",
]


def build_replacement_map():
    """Build byte-level replacement map for all double-encoded characters.

    Double-encoding: original UTF-8 bytes misread as cp1252, then re-encoded as UTF-8.
    """
    # Characters commonly found in Finnish YAML files
    chars = "äöåÄÖÅüéèøëïâêîôûàùñ"
    # Also include common symbols that might be in YAML
    chars += "→←↑↓–—''""\u2026"  # arrows, dashes, quotes, ellipsis
    chars += "×÷±°²³µΔΩπ"        # math/science symbols

    replacements = {}
    for c in chars:
        utf8_bytes = c.encode("utf-8")
        # Generate double-encoded form: each UTF-8 byte → cp1252 char → UTF-8
        double = b""
        try:
            for b in utf8_bytes:
                cp1252_char = bytes([b]).decode("cp1252")
                double += cp1252_char.encode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if double != utf8_bytes:
            replacements[double] = utf8_bytes
    return replacements


REPLACEMENTS = build_replacement_map()
# Sort by length descending so longer patterns match first
SORTED_PATTERNS = sorted(REPLACEMENTS.keys(), key=len, reverse=True)

fixed = 0
skipped = 0
errors = []


def fix_file(filepath: Path) -> bool:
    """Fix a single file. Returns True if fixed."""
    try:
        raw = filepath.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")

        if has_bom:
            raw = raw[3:]

        original = raw
        for pattern in SORTED_PATTERNS:
            if pattern in raw:
                raw = raw.replace(pattern, REPLACEMENTS[pattern])

        if raw != original or has_bom:
            filepath.write_bytes(raw)
            return True
        return False

    except Exception as e:
        errors.append(f"{filepath}: {e}")
        return False


for scan_dir in SCAN_DIRS:
    for root, dirs, files in os.walk(str(scan_dir)):
        for fname in files:
            if not fname.endswith((".yaml", ".yml")):
                continue
            filepath = Path(root) / fname
            if fix_file(filepath):
                rel = filepath.relative_to(PROJECT_DIR)
                print(f"  FIXED: {rel}")
                fixed += 1
            else:
                skipped += 1

print(f"\nResults: {fixed} fixed, {skipped} already clean, {len(errors)} errors")
for e in errors:
    print(f"  ERROR: {e}")
