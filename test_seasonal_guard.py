#!/usr/bin/env python3
"""Tests for Prompt 5: Seasonal Guard + MicroModel V1 Patterns.

Sections:
1. Seasonal rules YAML loading
2. SeasonalGuard.check() — deterministic matching
3. SeasonalGuard.check() — no false positives
4. SeasonalGuard.annotate_answer()
5. SeasonalGuard.filter_enrichment()
6. SeasonalGuard.queen_context()
7. Performance (<0.1ms)
8. Integration — backend chat.py
9. Integration — hivemind Round Table
10. Integration — fast_memory enrichment
11. MicroModel V1 — pattern tracking + promotion
12. MicroModel V1 — configs/micro_v1_patterns.json
"""
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

passed = 0
failed = 0
total = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


# ===================================================================
# Section 1: Seasonal rules YAML loading
# ===================================================================
print("\n=== Section 1: Seasonal rules YAML loading ===")

from core.seasonal_guard import SeasonalGuard

guard = SeasonalGuard()
check("rules loaded", guard.rule_count > 0, f"got {guard.rule_count} rules")
check("rules >= 9", guard.rule_count >= 9, f"got {guard.rule_count}")
check("month_name_fi works", guard.month_name_fi != "", f"got '{guard.month_name_fi}'")
check("month_name_en works", guard.month_name_en != "", f"got '{guard.month_name_en}'")

# Test month override
guard_jan = SeasonalGuard(month=1)
check("month override to January", guard_jan.current_month == 1)
check("January fi name", guard_jan.month_name_fi == "tammikuu",
      f"got '{guard_jan.month_name_fi}'")
check("January en name", guard_jan.month_name_en == "January",
      f"got '{guard_jan.month_name_en}'")


# ===================================================================
# Section 2: SeasonalGuard.check() — deterministic matching
# ===================================================================
print("\n=== Section 2: Seasonal rule violations detected ===")

# In January, "linkoa hunajaa nyt" should violate honey_extraction (valid Jun-Aug)
guard_jan = SeasonalGuard(month=1)
violations = guard_jan.check("linkoa hunajaa nyt")
check("Jan: 'linkoa hunajaa' -> violation", len(violations) > 0,
      f"got {len(violations)} violations")
if violations:
    check("  rule = honey_extraction", violations[0].rule == "honey_extraction",
          f"got {violations[0].rule}")
    check("  has reason_fi", len(violations[0].reason_fi) > 10)
    check("  has suggestion_fi", len(violations[0].suggestion_fi) > 10)
    check("  to_dict() works", "rule" in violations[0].to_dict())

# In January, "parveilunesto" should violate swarming (valid May-Jul)
violations = guard_jan.check("parveilunesto aika")
check("Jan: 'parveilunesto' -> violation", len(violations) > 0)

# In January, "oksaalihappo nyt" should NOT violate (valid Oct-Jan)
violations = guard_jan.check("oksaalihappo nyt")
check("Jan: 'oksaalihappo nyt' -> no violation (in season)", len(violations) == 0,
      f"got {len(violations)} violations")

# In July, "oksaalihappo nyt" SHOULD violate (not in Jul)
guard_jul = SeasonalGuard(month=7)
violations = guard_jul.check("tihkuta oksaalihappo nyt")
check("Jul: 'oksaalihappo nyt' -> violation", len(violations) > 0)

# In December, "avaa pesä nyt" should violate (winter no open)
guard_dec = SeasonalGuard(month=12)
violations = guard_dec.check("avaa pesä nyt")
check("Dec: 'avaa pesä nyt' -> violation", len(violations) > 0)

# In June, "linkoa hunajaa nyt" should NOT violate (in season)
guard_jun = SeasonalGuard(month=6)
violations = guard_jun.check("linkoa hunajaa nyt")
check("Jun: 'linkoa hunajaa' -> no violation (in season)", len(violations) == 0,
      f"got {len(violations)}")


# ===================================================================
# Section 3: No false positives
# ===================================================================
print("\n=== Section 3: No false positives ===")

# Generic text without forbidden keywords should never trigger
for month in range(1, 13):
    g = SeasonalGuard(month=month)
    v = g.check("Mehiläiset ovat fasiinoivia hyönteisiä")
    check(f"month {month:2d}: generic text -> no violation", len(v) == 0,
          f"got {len(v)} violations")

# Empty text
check("empty text -> no violation", len(guard.check("")) == 0)
check("None-like -> no violation", len(guard.check("   ")) == 0)


# ===================================================================
# Section 4: annotate_answer()
# ===================================================================
print("\n=== Section 4: annotate_answer() ===")

guard_jan = SeasonalGuard(month=1)
original = "Linkoa hunajaa nyt kun se on valmista."
annotated = guard_jan.annotate_answer(original)
check("annotated != original (has warning)", annotated != original)
check("annotated contains warning emoji", "\u26a0" in annotated)
check("annotated contains 'Kausihuomautus'", "Kausihuomautus" in annotated)
check("original text preserved", original in annotated)

# Clean answer should not be modified
clean = "Varroa on loispunkki."
check("clean answer unchanged", guard_jan.annotate_answer(clean) == clean)


# ===================================================================
# Section 5: filter_enrichment()
# ===================================================================
print("\n=== Section 5: filter_enrichment() ===")

guard_jan = SeasonalGuard(month=1)
ok, reason = guard_jan.filter_enrichment("Linkoa hunajaa nyt on tärkeää.")
check("Jan: enrichment 'linkoa nyt' rejected", not ok)
check("rejection has reason", len(reason) > 0, f"reason: {reason}")

ok2, reason2 = guard_jan.filter_enrichment("Varroa is a parasitic mite.")
check("generic fact passes filter", ok2)
check("no reason for pass", reason2 == "")


# ===================================================================
# Section 6: queen_context()
# ===================================================================
print("\n=== Section 6: queen_context() ===")

guard_feb = SeasonalGuard(month=2)
ctx = guard_feb.queen_context()
check("queen_context non-empty", len(ctx) > 50, f"len={len(ctx)}")
check("contains month name", "February" in ctx, f"ctx: {ctx[:80]}")
check("contains helmikuu", "helmikuu" in ctx)
check("contains 'season'", "season" in ctx.lower() or "Season" in ctx)
check("contains active/forbidden", "Active" in ctx or "Not in season" in ctx)
check("contains apply rules instruction", "seasonal rules" in ctx.lower())

# Different months give different contexts
guard_jul = SeasonalGuard(month=7)
ctx_jul = guard_jul.queen_context()
check("July context differs from Feb", ctx_jul != ctx)
check("July context has July", "July" in ctx_jul)


# ===================================================================
# Section 7: Performance (<0.1ms)
# ===================================================================
print("\n=== Section 7: Performance ===")

guard_perf = SeasonalGuard(month=1)
# Warm up
guard_perf.check("testi lause")

N = 1000
t0 = time.perf_counter()
for _ in range(N):
    guard_perf.check("linkoa hunajaa nyt tärkeä asia mehiläisille")
elapsed_ms = (time.perf_counter() - t0) * 1000
avg_ms = elapsed_ms / N
check(f"1000 checks in {elapsed_ms:.1f}ms (avg {avg_ms:.4f}ms)", avg_ms < 0.1,
      f"avg={avg_ms:.4f}ms")

t0 = time.perf_counter()
for _ in range(N):
    guard_perf.annotate_answer("Linkoa hunajaa nyt kun se on valmista.")
elapsed_ms = (time.perf_counter() - t0) * 1000
avg_ms = elapsed_ms / N
check(f"1000 annotate_answer in {elapsed_ms:.1f}ms", avg_ms < 0.2,
      f"avg={avg_ms:.4f}ms")


# ===================================================================
# Section 8: Integration — backend chat.py
# ===================================================================
print("\n=== Section 8: Backend chat.py integration ===")

import importlib
chat_mod = importlib.import_module("backend.routes.chat")

check("_get_seasonal_guard function exists",
      hasattr(chat_mod, '_get_seasonal_guard'))
check("_apply_seasonal_guard function exists",
      hasattr(chat_mod, '_apply_seasonal_guard'))

# Read source to verify integration points
chat_src = Path("backend/routes/chat.py").read_text(encoding="utf-8")
check("_apply_seasonal_guard called on YAML answers (fi)",
      "_apply_seasonal_guard(yaml_answer)" in chat_src)
check("_apply_seasonal_guard called on Layer 1 responses",
      "_apply_seasonal_guard(response)" in chat_src)


# ===================================================================
# Section 9: Integration — hivemind Round Table
# ===================================================================
print("\n=== Section 9: HiveMind Round Table integration ===")

hm_src = Path("hivemind.py").read_text(encoding="utf-8")
check("seasonal_guard imported in Round Table Queen synthesis",
      "seasonal_guard" in hm_src and "queen_context" in hm_src)
check("Queen gets seasonal context in synthesis prompt",
      "_sg.queen_context()" in hm_src)
check("Round Table synthesis filtered by seasonal guard",
      "filter_enrichment(synthesis)" in hm_src)


# ===================================================================
# Section 10: Integration — fast_memory enrichment
# ===================================================================
print("\n=== Section 10: fast_memory enrichment integration ===")

fm_src = Path("core/fast_memory.py").read_text(encoding="utf-8")
check("seasonal_guard imported in enrichment",
      "seasonal_guard" in fm_src)
check("filter_enrichment used in enrichment_cycle",
      "filter_enrichment" in fm_src)


# ===================================================================
# Section 11: MicroModel V1 — pattern tracking + promotion
# ===================================================================
print("\n=== Section 11: MicroModel V1 pattern tracking ===")

from core.micro_model import PatternMatchEngine

v1 = PatternMatchEngine(data_dir="data/micromodel_v1_test")

check("track_answer method exists", hasattr(v1, 'track_answer'))
check("promoted_count property exists", hasattr(v1, 'promoted_count'))

# Track 49 answers — should NOT promote yet
for i in range(49):
    v1.track_answer("mikä on varroa-kynnys?", "3 punkkia per 100 mehiläistä")

check("49 tracks -> not yet promoted",
      "varroa kynnys" not in str(v1._lookup.get(
          v1._normalize("mikä on varroa-kynnys?"), ("", 0))),
      "promoted too early")

# Track 1 more (50th) — should promote
v1.track_answer("mikä on varroa-kynnys?", "3 punkkia per 100 mehiläistä")

key = v1._normalize("mikä on varroa-kynnys?")
check("50 tracks -> promoted to lookup",
      key in v1._lookup, f"key='{key}' not in lookup")

if key in v1._lookup:
    answer, conf = v1._lookup[key]
    check("promoted answer correct", answer == "3 punkkia per 100 mehiläistä")
    check("promoted confidence = 0.92", abs(conf - 0.92) < 0.01)

# Predict should find it
result = v1.predict("mikä on varroa-kynnys?")
check("predict finds promoted pattern", result is not None)
if result:
    check("predict method = v1_exact", result["method"] == "v1_exact")

# Different answers should not promote easily
for i in range(30):
    v1.track_answer("toinen kysymys", "vastaus A" if i % 3 == 0 else "vastaus B")
check("inconsistent answers not promoted",
      v1._normalize("toinen kysymys") not in v1._lookup or
      v1._answer_tracker.get(v1._normalize("toinen kysymys"), {}).get("count", 0) < 50)


# ===================================================================
# Section 12: configs/micro_v1_patterns.json
# ===================================================================
print("\n=== Section 12: configs/micro_v1_patterns.json ===")

from core.micro_model import _CONFIGS_V1_PATH

check("_CONFIGS_V1_PATH points to configs/",
      "configs" in str(_CONFIGS_V1_PATH))

# The promotion in section 11 should have written the file
if _CONFIGS_V1_PATH.exists():
    with open(_CONFIGS_V1_PATH, encoding="utf-8") as f:
        data = json.load(f)
    check("configs file has 'promoted' key", "promoted" in data)
    check("configs file has timestamp", "timestamp" in data)
    promoted = data.get("promoted", {})
    check("configs file has >= 1 promoted pattern", len(promoted) >= 1,
          f"got {len(promoted)}")
    # Verify structure
    if promoted:
        first_key = next(iter(promoted))
        entry = promoted[first_key]
        check("entry has 'answer'", "answer" in entry)
        check("entry has 'confidence'", "confidence" in entry)
        check("entry has 'promoted_at'", "promoted_at" in entry)
else:
    check("configs/micro_v1_patterns.json created", False,
          "file does not exist after promotion")

# Verify a new V1 engine loads the promoted patterns
v1_new = PatternMatchEngine(data_dir="data/micromodel_v1_test_empty")
# It should have loaded configs patterns
result = v1_new.predict("mikä on varroa-kynnys?")
check("new V1 engine finds promoted pattern from configs", result is not None)

# Stats should include promoted_count
stats = v1.stats
check("stats has promoted_count", "promoted_count" in stats)
check("stats has tracked_questions", "tracked_questions" in stats)


# ===================================================================
# Cleanup test artifacts
# ===================================================================
import shutil
for d in ["data/micromodel_v1_test", "data/micromodel_v1_test_empty"]:
    if Path(d).exists():
        shutil.rmtree(d, ignore_errors=True)


# ===================================================================
# Summary
# ===================================================================
print(f"\n{'='*60}")
print(f"RESULTS: {passed}/{total} passed, {failed} failed")
if failed == 0:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {failed}")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
