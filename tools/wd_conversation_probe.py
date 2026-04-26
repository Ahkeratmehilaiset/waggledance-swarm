#!/usr/bin/env python3
"""wd_conversation_probe — Phase 9 §V CLI driver.

Probes the conversation layer with a meta-question. Reads optional
self_model + presence_log, runs context_synthesizer, and emits the
meta-dialogue response with forbidden-pattern check.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.conversation import (  # noqa: E402
    context_synthesizer as cs,
    meta_dialogue as md,
    presence_log as pl,
)


DEFAULT_FORBIDDEN_PATTERNS = (
    ROOT / "waggledance" / "core" / "identity" / "forbidden_patterns.yaml"
)


def _load_optional_json(p: Path | None) -> dict | None:
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", type=str,
                    default="what_changed_since_last_time",
                    choices=md.META_QUESTION_KINDS)
    ap.add_argument("--self-model-path", type=Path, default=None)
    ap.add_argument("--prev-self-model-path", type=Path, default=None)
    ap.add_argument("--presence-log-path", type=Path, default=None)
    ap.add_argument("--forbidden-patterns-path", type=Path,
                    default=DEFAULT_FORBIDDEN_PATTERNS)
    ap.add_argument("--ts", type=str, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    self_model = _load_optional_json(args.self_model_path)
    prev_self_model = _load_optional_json(args.prev_self_model_path)
    presence = pl.read_entries(args.presence_log_path) \
        if args.presence_log_path else []

    ctx = cs.synthesize(
        recent_presence=presence,
        self_model=self_model,
        prev_self_model=prev_self_model,
        forbidden_patterns_path=args.forbidden_patterns_path,
    )
    response = md.respond(args.question, ctx)

    if args.json:
        print(json.dumps(response.to_dict(), indent=2, sort_keys=True))
    else:
        print("=== wd_conversation_probe ===")
        print(f"question: {args.question}")
        print(f"is_clean: {response.is_clean}")
        print()
        print(response.rendered_text)
        if response.pattern_violations:
            print()
            print("PATTERN VIOLATIONS:")
            for v in response.pattern_violations:
                print(f"  - [{v.kind}] {v.pattern!r} at offset {v.location}")
    return 0 if response.is_clean else 1


if __name__ == "__main__":
    sys.exit(main())
