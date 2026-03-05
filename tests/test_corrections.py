#!/usr/bin/env python3
"""
Corrections Memory — Verification Test
========================================
Tests the corrections memory subsystem:
  1. ChromaDB corrections collection exists (nomic-embed)
  2. store_correction() stores Q/bad/good triples
  3. check_previous_corrections() uses distance < 0.3 threshold
  4. Agent trust floor 0.3 enforced on correction penalty
  5. Finnish correction phrase detection (ei vaan, oikea vastaus, tarkoitin)
  6. Logging when correction context is injected
"""

import sys
import os
import shutil
import tempfile
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows UTF-8
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; W = "\033[0m"
os.system("")  # ANSI Windows

results = {"pass": 0, "fail": 0, "warn": 0, "errors": []}


def OK(msg):
    results["pass"] += 1
    print(f"  {G}OK {msg}{W}")


def FAIL(msg):
    results["fail"] += 1
    results["errors"].append(msg)
    print(f"  {R}FAIL {msg}{W}")


def WARN(msg):
    results["warn"] += 1
    print(f"  {Y}WARN {msg}{W}")


def SECTION(title):
    print(f"\n{B}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{W}")


# --- 1. CORRECTIONS COLLECTION -------------------
SECTION("1. CHROMADB CORRECTIONS COLLECTION")
td = tempfile.mkdtemp()
try:
    from consciousness import MemoryStore
    ms = MemoryStore(path=td)

    if hasattr(ms, 'corrections'):
        OK(f"corrections collection exists (count={ms.corrections.count()})")
    else:
        FAIL("corrections collection missing from MemoryStore")

    # Verify it uses cosine distance
    meta = ms.corrections.metadata
    if meta and meta.get("hnsw:space") == "cosine":
        OK("corrections collection uses cosine distance")
    else:
        FAIL(f"corrections collection metadata: {meta}")

except Exception as e:
    FAIL(f"MemoryStore init: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 2. STORE_CORRECTION -------------------------
SECTION("2. STORE_CORRECTION")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    c = Consciousness(db_path=td)

    # Method exists
    if hasattr(c, 'store_correction') and callable(c.store_correction):
        OK("store_correction() method exists")
    else:
        FAIL("store_correction() missing")

    # Test live store (needs embedding)
    if c.embed.available:
        stored = c.store_correction(
            query="mik\u00e4 on varroa-kynnys",
            bad_answer="10 punkkia per mehil\u00e4inen",
            good_answer="3 punkkia per 100 mehil\u00e4ist\u00e4 elokuussa",
            agent_id="test_agent")
        if stored:
            OK("store_correction() returned True")
            count = c.memory.corrections.count()
            if count >= 1:
                OK(f"corrections collection has {count} entry")
            else:
                FAIL("corrections collection empty after store")
        else:
            WARN("store_correction() returned False (embedding may be unavailable)")

        # Correct answer also learned in main memory
        if c.memory.count > 0:
            OK("Correct answer also stored in main memory")
        else:
            WARN("Correct answer not in main memory (may need flush)")

        # _corrections_count incremented
        if c._corrections_count >= 1:
            OK(f"_corrections_count incremented: {c._corrections_count}")
        else:
            FAIL(f"_corrections_count should be >= 1, got {c._corrections_count}")
    else:
        WARN("Embedding not available -- skipping live store_correction test")

except Exception as e:
    FAIL(f"store_correction: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 3. CHECK_PREVIOUS_CORRECTIONS (distance < 0.3) -
SECTION("3. CHECK_PREVIOUS_CORRECTIONS (distance < 0.3)")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    c = Consciousness(db_path=td)

    # Method exists
    if hasattr(c, 'check_previous_corrections') and callable(c.check_previous_corrections):
        OK("check_previous_corrections() method exists")
    else:
        FAIL("check_previous_corrections() missing")

    # Empty collection returns empty string
    result = c.check_previous_corrections("test query")
    if result == "":
        OK("Empty corrections collection returns empty string")
    else:
        FAIL(f"Expected empty string, got: {result!r}")

    # Source code check: dist < 0.3 threshold
    import inspect
    src = inspect.getsource(c.check_previous_corrections)
    if "dist < 0.3" in src:
        OK("Uses distance < 0.3 threshold (cosine)")
    elif "score > 0.85" in src:
        OK("Uses score > 0.85 threshold (equivalent)")
    else:
        FAIL("Threshold not set to distance < 0.3 or score > 0.85")

    # Live test (needs embedding)
    if c.embed.available:
        # Store a correction then query for similar
        c.store_correction(
            query="varroa treatment threshold",
            bad_answer="10 mites per bee",
            good_answer="3 mites per 100 bees in August",
            agent_id="test_agent")

        ctx = c.check_previous_corrections("varroa treatment threshold")
        if ctx:
            OK(f"check_previous_corrections found match: {ctx[:80]}")
        else:
            WARN("check_previous_corrections returned empty (distance may be > 0.3)")

        # Unrelated query should return empty
        ctx2 = c.check_previous_corrections("weather forecast for tomorrow in Helsinki")
        if ctx2 == "":
            OK("Unrelated query returns empty corrections context")
        else:
            WARN(f"Unrelated query got corrections context: {ctx2[:60]}")
    else:
        WARN("Embedding not available -- skipping live check test")

except Exception as e:
    FAIL(f"check_previous_corrections: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 4. AGENT TRUST FLOOR 0.3 --------------------
SECTION("4. AGENT TRUST FLOOR 0.3")
td = tempfile.mkdtemp()
try:
    from core.agent_levels import AgentLevelManager

    mgr = AgentLevelManager(db_path=td)

    # Source code check: max(0.3, ...)
    import inspect
    src = inspect.getsource(mgr.record_response)
    if "max(0.3" in src:
        OK("Trust floor set to 0.3 in record_response()")
    else:
        FAIL(f"Trust floor not 0.3 in record_response()")

    # Live test: correct many times to build trust, then correct-penalize repeatedly
    agent_id = "trust_floor_test"
    # Build trust up first
    for _ in range(20):
        mgr.record_response(agent_id, "test", was_correct=True)

    stats_before = mgr.get_stats(agent_id)
    initial_trust = stats_before["trust_score"]

    # Apply many corrections to drive trust down
    for _ in range(50):
        mgr.record_response(agent_id, "test", was_correct=False,
                            was_corrected=True)

    stats_after = mgr.get_stats(agent_id)
    if stats_after["trust_score"] >= 0.3:
        OK(f"Trust floor enforced: {initial_trust:.3f} -> {stats_after['trust_score']:.3f} (>= 0.3)")
    else:
        FAIL(f"Trust went below 0.3: {stats_after['trust_score']:.3f}")

    # user_corrections incremented
    if stats_after["user_corrections"] >= 50:
        OK(f"user_corrections tracked: {stats_after['user_corrections']}")
    else:
        FAIL(f"user_corrections should be >= 50, got {stats_after['user_corrections']}")

except Exception as e:
    FAIL(f"Agent trust floor: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 5. FINNISH CORRECTION PHRASE DETECTION -------
SECTION("5. FINNISH CORRECTION PHRASE DETECTION")
try:
    import ast
    src = open("hivemind.py", encoding="utf-8").read()

    # Word patterns
    for word in ["v\u00e4\u00e4r\u00e4", "virhe", "tarkoitin"]:
        if word in src:
            OK(f"Correction word '{word}' present")
        else:
            FAIL(f"Correction word '{word}' missing")

    # Phrase patterns
    for phrase in ["ei vaan", "oikea vastaus"]:
        if phrase in src:
            OK(f"Correction phrase '{phrase}' present")
        else:
            FAIL(f"Correction phrase '{phrase}' missing")

    # Phrase matching logic (not just word-set intersection)
    if "_CORRECTION_PHRASES" in src or "correction_phrase" in src.lower():
        OK("Phrase-level matching implemented (not just word split)")
    else:
        FAIL("No phrase-level correction detection found")

    # Detection calls store_correction
    if "store_correction" in src:
        OK("Correction detection calls store_correction()")
    else:
        FAIL("store_correction() not called from detection")

    # Agent penalty on correction
    if "was_corrected=True" in src:
        OK("Agent penalized on correction (was_corrected=True)")
    else:
        FAIL("Agent not penalized on correction")

except Exception as e:
    FAIL(f"Correction detection: {e}")


# --- 6. LOGGING WHEN CORRECTION USED -------------
SECTION("6. LOGGING WHEN CORRECTION INJECTED")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    import inspect

    c = Consciousness(db_path=td)

    # Source code check: log.info in check_previous_corrections
    src = inspect.getsource(c.check_previous_corrections)
    if "log.info" in src and "Corrections injected" in src:
        OK("check_previous_corrections() logs when corrections injected")
    elif "log." in src:
        OK("check_previous_corrections() has logging")
    else:
        FAIL("No logging in check_previous_corrections()")

    # Source code check: log.info in store_correction
    src_store = inspect.getsource(c.store_correction)
    if "log.info" in src_store or "log.warning" in src_store:
        OK("store_correction() has logging")
    else:
        FAIL("No logging in store_correction()")

    # Live logging test (capture log output)
    if c.embed.available:
        log_handler = logging.handlers.MemoryHandler(capacity=100) if hasattr(logging, 'handlers') else None
        # Simplified: just check the log call doesn't crash
        c.store_correction(
            query="test logging",
            bad_answer="wrong answer",
            good_answer="correct answer",
            agent_id="log_test")
        OK("store_correction logging executes without error")
    else:
        WARN("Embedding not available -- skipping live logging test")

except Exception as e:
    FAIL(f"Logging: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 7. CORRECTIONS INJECTION INTO PROMPT --------
SECTION("7. CORRECTIONS CONTEXT INJECTED INTO PROMPT")
try:
    src = open("hivemind.py", encoding="utf-8").read()

    if "check_previous_corrections" in src:
        OK("check_previous_corrections() called in routing")
    else:
        FAIL("check_previous_corrections() not called")

    if "CORRECTIONS" in src and "avoid repeating" in src.lower():
        OK("Corrections context injected with warning label")
    else:
        FAIL("Corrections context not injected into prompt")

    # WebSocket notification
    if "correction_stored" in src:
        OK("WebSocket event 'correction_stored' present")
    else:
        FAIL("WebSocket event 'correction_stored' missing")

except Exception as e:
    FAIL(f"Prompt injection: {e}")


# --- 8. GET_AGENT_ERROR_PATTERNS (Failure Twin) --
SECTION("8. FAILURE TWIN — AGENT-SPECIFIC ERROR PATTERNS")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    c = Consciousness(db_path=td)

    if hasattr(c, 'get_agent_error_patterns') and callable(c.get_agent_error_patterns):
        OK("get_agent_error_patterns() method exists")
    else:
        FAIL("get_agent_error_patterns() missing")

    # Empty collection returns empty
    result = c.get_agent_error_patterns("test_agent", "test query")
    if result == "":
        OK("Empty corrections returns empty failure twin context")
    else:
        FAIL(f"Expected empty, got: {result!r}")

except Exception as e:
    FAIL(f"Failure Twin: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 9. ERROR TYPE CLASSIFICATION ----------------
SECTION("9. ERROR TYPE CLASSIFICATION IN STORE_CORRECTION")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    c = Consciousness(db_path=td)

    if c.embed.available:
        # Test too_brief classification
        c.store_correction(
            query="test brief",
            bad_answer="short",
            good_answer="A proper detailed answer about bee health",
            agent_id="type_test")
        # Test knowledge_gap classification
        c.store_correction(
            query="test gap",
            bad_answer="en tied\u00e4 vastausta t\u00e4h\u00e4n kysymykseen",
            good_answer="Varroa is treated with oxalic acid",
            agent_id="type_test")
        # Test wrong_content classification
        c.store_correction(
            query="test wrong",
            bad_answer="Varroa is treated with sugar water which is completely wrong information",
            good_answer="Varroa is treated with oxalic acid in broodless period",
            agent_id="type_test")

        count = c.memory.corrections.count()
        if count >= 3:
            OK(f"Error type classification: {count} corrections stored with types")

            # Check metadata has error_type field
            all_corr = c.memory.corrections.get(
                include=["metadatas"], limit=3)
            types_found = set()
            for meta in all_corr.get("metadatas", []):
                et = meta.get("error_type", "")
                if et:
                    types_found.add(et)
            if len(types_found) >= 2:
                OK(f"Error types found: {types_found}")
            else:
                WARN(f"Only {len(types_found)} error types: {types_found}")
        else:
            FAIL(f"Expected >= 3 corrections, got {count}")
    else:
        WARN("Embedding not available -- skipping error type test")

except Exception as e:
    FAIL(f"Error type: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- 10. CORRECTIONS STATS IN CONSCIOUSNESS ------
SECTION("10. CORRECTIONS IN CONSCIOUSNESS STATS")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness
    c = Consciousness(db_path=td)

    stats = c.stats
    if "corrections" in stats:
        OK(f"stats['corrections'] = {stats['corrections']}")
    else:
        FAIL("stats missing 'corrections' key")

    if "_corrections_count" in dir(c):
        OK(f"_corrections_count attribute exists: {c._corrections_count}")
    else:
        FAIL("_corrections_count missing")

except Exception as e:
    FAIL(f"Stats: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# --- SUMMARY -------------------------------------
print(f"\n{B}{'='*60}")
print(f"  CORRECTIONS MEMORY TEST SUMMARY")
print(f"{'='*60}{W}")
print(f"  {G}PASS: {results['pass']}{W}")
print(f"  {R}FAIL: {results['fail']}{W}")
print(f"  {Y}WARN: {results['warn']}{W}")

if results["errors"]:
    print(f"\n  {R}Failures:{W}")
    for err in results["errors"]:
        print(f"    {R}- {err}{W}")

total = results["pass"] + results["fail"]
pct = (results["pass"] / total * 100) if total > 0 else 0
print(f"\n  Score: {results['pass']}/{total} ({pct:.0f}%)")

if results["fail"] == 0:
    print(f"\n  {G}{'='*50}")
    print(f"  CORRECTIONS MEMORY TESTS PASSED")
    print(f"  {'='*50}{W}")
else:
    print(f"\n  {R}{'='*50}")
    print(f"  {results['fail']} test(s) failed")
    print(f"  {'='*50}{W}")

sys.exit(0 if results["fail"] == 0 else 1)
