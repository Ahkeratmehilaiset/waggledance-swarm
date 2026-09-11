# SPDX-License-Identifier: BUSL-1.1
"""Recognize only existing, verbatim public sentinel statements in Python diffs.

This is not a general secret scanner or an approval mechanism. Metadata and
bridge events must continue through their ordinary strict scanners. Never
replace the original diff with a filtered copy: review and hashing use all bytes.
"""
from __future__ import annotations

import io
import re
import tokenize

PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")

# Finite source statements already reviewed in bridge PRs 1681 and 1682.
# No caller-supplied exceptions, wildcard paths, or partial-statement matches.
_PUBLIC_STATEMENTS = frozenset((
    'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")',
    'if any(marker in rendered for marker in ("PRIVATE_MARKER", "_DO_NOT_LEAK")):',
    'row["message"] = "PRIVATE_MARKER"',
    'assert "PRIVATE_MARKER" not in captured.out + captured.err',
))
_HEADER = re.compile(r"diff --git a/([A-Za-z0-9_./-]+) b/\1\Z")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\Z")


def _marker(text: str) -> str | None:
    return next((marker for marker in PRIVATE_MARKERS if marker in text), None)


def _public_statement(text: str) -> bool:
    statement = text.lstrip(" \t")
    if statement in _PUBLIC_STATEMENTS:
        return True
    # Retain the existing bare plural-identifier allowance, but only within
    # an actual Python hunk. Quoted values and larger identifiers remain data.
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(statement).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False
    marked = [token for token in tokens if _marker(token.string)]
    return bool(marked) and all(
        token.type == tokenize.NAME and token.string == "PRIVATE_MARKERS"
        for token in marked
    )


def find_diff_private_marker(diff: str) -> str | None:
    """Fail closed outside complete, count-consistent Python unified-diff hunks."""
    if not isinstance(diff, str):
        raise TypeError("diff must be a string")
    first = _marker(diff)
    if first is None:
        return None
    python_file = False
    old_left = new_left = 0
    in_hunk = False
    for line in diff.split("\n"):
        header = _HEADER.fullmatch(line)
        if header:
            if old_left or new_left:
                return first
            path = header[1]
            python_file = path.endswith(".py") and all(part not in ("", ".", "..") for part in path.split("/"))
            in_hunk = False
        elif line.startswith("diff --git "):
            if old_left or new_left:
                return first
            python_file = in_hunk = False
        hunk = _HUNK.fullmatch(line)
        if hunk:
            if old_left or new_left:
                return first
            old_left = int(hunk[2]) if hunk[2] is not None else 1
            new_left = int(hunk[4]) if hunk[4] is not None else 1
            in_hunk = True
            if _marker(line):
                return first
            continue
        content = in_hunk and bool(old_left or new_left) and line[:1] in ("+", "-", " ")
        if content:
            prefix = line[0]
            old_left -= prefix != "+"
            new_left -= prefix != "-"
            if old_left < 0 or new_left < 0:
                return first
        marker = _marker(line)
        if marker and not (content and python_file and _public_statement(line[1:])):
            return marker
        if in_hunk and (old_left or new_left) and not content and line != "\\ No newline at end of file":
            return first
    return first if old_left or new_left else None
