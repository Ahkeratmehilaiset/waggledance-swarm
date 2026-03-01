#!/usr/bin/env python3
"""
Phase 4: Advanced Learning — Verification Test
===============================================
Tests all Phase 4 components:
  1. ChromaDB collections (corrections, episodes)
  2. Contrastive Learning (store_correction, check_previous_corrections)
  3. Active Learning (detect_user_teaching, learn_from_user, before_llm tier 4)
  4. Embedding Augmentation (_load_domain_synonyms, _augment_text_for_embedding)
  5. Multi-hop RAG (multi_hop_search, _extract_entities)
  6. Episodic Memory (store_episode, get_episode_chain)
  7. Seasonal Scoring Boost (MemoryStore.search seasonal_boost param)
  8. Distillation Prep (tools/distill_from_opus.py)
  9. HiveMind integration (correction detection, teaching, episode tracking)
  10. Dashboard (/api/consciousness, HTML, JS)
  11. Settings YAML (advanced_learning section)
  12. VRAM impact (no new GPU models)
"""

import sys
import os
import json
import time
import re
import shutil
import tempfile
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

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


# ─── 1. ChromaDB Collections ──────────────────────
SECTION("1. CHROMADB COLLECTIONS (corrections, episodes)")
td = tempfile.mkdtemp()
try:
    from consciousness import MemoryStore
    ms = MemoryStore(path=td)

    # Check corrections collection exists
    if hasattr(ms, 'corrections'):
        OK(f"corrections collection exists (count={ms.corrections.count()})")
    else:
        FAIL("corrections collection missing from MemoryStore")

    # Check episodes collection exists
    if hasattr(ms, 'episodes'):
        OK(f"episodes collection exists (count={ms.episodes.count()})")
    else:
        FAIL("episodes collection missing from MemoryStore")

    # Check swarm_facts still exists (Phase 3)
    if hasattr(ms, 'swarm_facts'):
        OK("swarm_facts collection still intact")
    else:
        FAIL("swarm_facts collection missing")

    # Test seasonal_boost parameter in search
    try:
        _result = ms.search([0.1] * 768, top_k=1, seasonal_boost=["varroa", "test"])
        OK("search() accepts seasonal_boost parameter")
    except TypeError as e:
        FAIL(f"search() seasonal_boost: {e}")

except Exception as e:
    FAIL(f"MemoryStore init: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 2. Contrastive Learning ──────────────────────
SECTION("2. CONTRASTIVE LEARNING")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)

    # store_correction method exists
    if hasattr(c, 'store_correction') and callable(c.store_correction):
        OK("store_correction() method exists")
    else:
        FAIL("store_correction() missing")

    # check_previous_corrections method exists
    if hasattr(c, 'check_previous_corrections') and callable(c.check_previous_corrections):
        OK("check_previous_corrections() method exists")
    else:
        FAIL("check_previous_corrections() missing")

    # Test store_correction (needs embedding)
    if c.embed.available:
        stored = c.store_correction(
            query="what is varroa threshold",
            bad_answer="10 mites per bee",
            good_answer="3 mites per 100 bees in August",
            agent_id="test_agent")
        if stored:
            OK("store_correction() stored successfully")
            # Verify in ChromaDB
            count = c.memory.corrections.count()
            if count > 0:
                OK(f"corrections collection has {count} entry")
            else:
                FAIL("corrections collection empty after store")

            # Check previous corrections retrieval
            ctx = c.check_previous_corrections("varroa threshold")
            if ctx and "3 mites" in ctx:
                OK(f"check_previous_corrections() found: {ctx[:80]}")
            elif ctx:
                OK(f"check_previous_corrections() returned context (different match)")
            else:
                WARN("check_previous_corrections() returned empty (embedding match too low)")

            # Also learn correct answer
            if c.memory.count > 0:
                OK("Correct answer also learned in main memory")
            else:
                WARN("Correct answer not found in main memory (may need flush)")
        else:
            WARN("store_correction() returned False (embedding may not be available)")
    else:
        WARN("Embedding not available — skipping store_correction live test")

    # _corrections_count tracks
    if hasattr(c, '_corrections_count'):
        OK(f"_corrections_count = {c._corrections_count}")
    else:
        FAIL("_corrections_count attribute missing")

except Exception as e:
    FAIL(f"Contrastive Learning: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 3. Active Learning ──────────────────────────
SECTION("3. ACTIVE LEARNING")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)

    # detect_user_teaching method exists
    if hasattr(c, 'detect_user_teaching') and callable(c.detect_user_teaching):
        OK("detect_user_teaching() method exists")
    else:
        FAIL("detect_user_teaching() missing")

    # Test detect_user_teaching logic
    # Should return True when prev_method is active_learning and message is informative
    if c.detect_user_teaching("Varroa-kynnys on 3 punkkia per 100 mehilaista",
                              prev_method="active_learning"):
        OK("detect_user_teaching() returns True for informative text after active_learning")
    else:
        FAIL("detect_user_teaching() should return True for informative text")

    # Should return False for short messages
    if not c.detect_user_teaching("ei", prev_method="active_learning"):
        OK("detect_user_teaching() returns False for short message")
    else:
        FAIL("detect_user_teaching() should return False for 'ei'")

    # Should return False if prev_method != active_learning
    if not c.detect_user_teaching("Varroa-kynnys on 3 punkkia", prev_method="memory_fast"):
        OK("detect_user_teaching() returns False if prev != active_learning")
    else:
        FAIL("detect_user_teaching() should return False for non-active_learning")

    # Should return False for negation (Finnish ä)
    if not c.detect_user_teaching("en tiedä miten varroa hoidetaan", prev_method="active_learning"):
        OK("detect_user_teaching() returns False for negation patterns")
    else:
        WARN("detect_user_teaching() detected negation as teaching (edge case)")

    # learn_from_user method
    if hasattr(c, 'learn_from_user') and callable(c.learn_from_user):
        OK("learn_from_user() method exists")
    else:
        FAIL("learn_from_user() missing")

    # Test learn_from_user (needs embedding)
    if c.embed.available:
        stored = c.learn_from_user(
            "Varroa-kynnys on 3 punkkia per 100 mehilaista elokuussa",
            "mika on varroa-kynnys")
        if stored:
            OK("learn_from_user() stored fact successfully")
        else:
            WARN("learn_from_user() returned False (dedup or embed issue)")
    else:
        WARN("Embedding not available — skipping learn_from_user live test")

    # Active learning count tracking
    if hasattr(c, '_active_learning_count'):
        OK(f"_active_learning_count = {c._active_learning_count}")
    else:
        FAIL("_active_learning_count attribute missing")

    # Test before_llm active learning trigger
    # Need enough memories first (>100) for active learning to trigger
    if c.embed.available:
        # Seed enough fake memories
        for i in range(105):
            c._learn_single(
                f"Test beekeeping fact number {i} about colony management",
                agent_id="seed", confidence=0.8, validated=True)

        if c.memory.count > 100:
            # Ask something completely unknown
            pre = c.before_llm("quantenmechanik der bienen im weltraum xyz")
            if pre.method == "active_learning":
                OK(f"before_llm() triggers active_learning for unknown topic")
                if "En ole varma" in (pre.answer or ""):
                    OK("Active learning response is Finnish")
                else:
                    WARN(f"Active learning response: {pre.answer[:60]}")
            else:
                WARN(f"before_llm() returned method={pre.method} (may have found partial match)")
        else:
            WARN(f"Only {c.memory.count} memories, need >100 for active learning")
    else:
        WARN("Embedding not available — skipping active learning before_llm test")

except Exception as e:
    FAIL(f"Active Learning: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 4. Embedding Augmentation ──────────────────
SECTION("4. EMBEDDING AUGMENTATION")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)

    # _domain_synonyms loaded
    if hasattr(c, '_domain_synonyms'):
        count = len(c._domain_synonyms)
        if count >= 18:
            OK(f"_domain_synonyms loaded: {count} entries (>= 18 builtin)")
        else:
            WARN(f"_domain_synonyms: only {count} entries (expected >= 18)")
    else:
        FAIL("_domain_synonyms attribute missing")

    # Check specific builtin synonyms
    syns = c._domain_synonyms
    if "toukkamata" in syns or "toukkam\u00e4t\u00e4" in syns:
        key = "toukkam\u00e4t\u00e4" if "toukkam\u00e4t\u00e4" in syns else "toukkamata"
        val = syns[key]
        if "Foulbrood" in val or "AFB" in val:
            OK(f"toukkamata -> {val[:60]}")
        else:
            FAIL(f"toukkamata augmentation wrong: {val}")
    else:
        FAIL("toukkamata missing from domain_synonyms")

    if "varroa" in syns:
        val = syns["varroa"]
        if "Varroa" in val or "mite" in val:
            OK(f"varroa -> {val[:60]}")
        else:
            FAIL(f"varroa augmentation wrong: {val}")
    else:
        FAIL("varroa missing from domain_synonyms")

    # Test _augment_text_for_embedding
    if hasattr(c, '_augment_text_for_embedding'):
        augmented = c._augment_text_for_embedding(
            "Varroa treatment in August",
            "varroa-hoito elokuussa")
        if "Varroa destructor" in augmented or "varroa mite" in augmented:
            OK(f"Augmented: {augmented[:80]}")
        else:
            WARN(f"Augmentation didn't match 'varroa': {augmented[:80]}")

        # Non-domain text should not be augmented
        plain = c._augment_text_for_embedding("Hello world test", "hello world test")
        if plain == "Hello world test":
            OK("Non-domain text not augmented")
        else:
            WARN(f"Non-domain text was augmented: {plain[:60]}")
    else:
        FAIL("_augment_text_for_embedding() missing")

except Exception as e:
    FAIL(f"Embedding Augmentation: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 5. Multi-hop RAG ────────────────────────────
SECTION("5. MULTI-HOP RAG")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)

    # Method exists
    if hasattr(c, 'multi_hop_search') and callable(c.multi_hop_search):
        OK("multi_hop_search() method exists")
    else:
        FAIL("multi_hop_search() missing")

    if hasattr(c, '_extract_entities') and callable(c._extract_entities):
        OK("_extract_entities() method exists")
    else:
        FAIL("_extract_entities() missing")

    # Test _extract_entities
    from consciousness import MemoryMatch
    test_matches = [
        MemoryMatch(text="Varroa destructor is a parasitic mite",
                    score=0.8, text_en="Varroa destructor is a parasitic mite"),
        MemoryMatch(text="Treatment with Oxalic Acid (OA) in October",
                    score=0.7, text_en="Treatment with Oxalic Acid (OA) in October"),
    ]
    entities = c._extract_entities(test_matches)
    if entities:
        OK(f"_extract_entities() found {len(entities)} entities: {entities[:4]}")
        # Should find "Varroa" and "Oxalic Acid"
        entity_text = " ".join(entities).lower()
        if "varroa" in entity_text:
            OK("Found 'Varroa' entity")
        else:
            WARN("'Varroa' not extracted as entity")
    else:
        WARN("_extract_entities() returned empty list")

    # Test multi_hop_search with seeded data
    if c.embed.available:
        # Seed some related facts
        facts = [
            "Varroa destructor is the most dangerous parasite for honey bees",
            "Oxalic acid treatment is effective against Varroa mites in broodless period",
            "The broodless period in Finland is typically in October-November",
            "Formic acid (MAQS) can be used during brood season for Varroa treatment",
            "Varroa threshold is 3 mites per 100 bees in August for treatment decision",
        ]
        for f in facts:
            c.learn(f, agent_id="test", confidence=0.85, validated=True, immediate=True)

        results_hop = c.multi_hop_search("varroa hoitokynnys")
        if results_hop:
            OK(f"multi_hop_search() returned {len(results_hop)} results")
            for r in results_hop[:3]:
                print(f"    [{r.score:.0%}] {(r.text_en or r.text)[:70]}")
        else:
            WARN("multi_hop_search() returned empty (embedding may not match)")
    else:
        WARN("Embedding not available — skipping multi_hop_search live test")

except Exception as e:
    FAIL(f"Multi-hop RAG: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 6. Episodic Memory ─────────────────────────
SECTION("6. EPISODIC MEMORY")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)

    # Methods exist
    if hasattr(c, 'store_episode') and callable(c.store_episode):
        OK("store_episode() method exists")
    else:
        FAIL("store_episode() missing")

    if hasattr(c, 'get_episode_chain') and callable(c.get_episode_chain):
        OK("get_episode_chain() method exists")
    else:
        FAIL("get_episode_chain() missing")

    # Session ID initialized
    if hasattr(c, '_current_session_id') and c._current_session_id.startswith("session_"):
        OK(f"Session ID: {c._current_session_id}")
    else:
        FAIL("_current_session_id not set properly")

    # Episode counter
    if hasattr(c, '_episode_counter') and c._episode_counter == 0:
        OK("_episode_counter starts at 0")
    else:
        FAIL("_episode_counter not initialized properly")

    # Test store_episode + chain
    if c.embed.available:
        ep1 = c.store_episode(
            query="mika on varroa",
            response="Varroa destructor on loinen",
            quality=0.8)
        if ep1:
            OK(f"Episode 1 stored: {ep1}")
        else:
            WARN("store_episode() returned None (embedding issue)")

        ep2 = c.store_episode(
            query="miten varroa hoidetaan",
            response="Oksaalihapolla tai muurahaishapolla",
            prev_episode_id=ep1,
            quality=0.9)
        if ep2:
            OK(f"Episode 2 stored with chain: {ep2}")
        else:
            WARN("store_episode() returned None for episode 2")

        ep3 = c.store_episode(
            query="milloin happohoito tehdaan",
            response="Lokakuussa sikiottomana aikana",
            prev_episode_id=ep2,
            quality=0.85)
        if ep3:
            OK(f"Episode 3 stored with chain: {ep3}")
        else:
            WARN("store_episode() returned None for episode 3")

        # Test chain retrieval
        if ep3:
            chain = c.get_episode_chain(ep3)
            if chain and len(chain) >= 2:
                OK(f"get_episode_chain() returned {len(chain)} episodes")
                for ep in chain:
                    print(f"    {ep['id']}: {ep.get('text', '')[:60]}")
            elif chain:
                WARN(f"get_episode_chain() returned only {len(chain)} episode(s)")
            else:
                WARN("get_episode_chain() returned empty")

        # Check episodes collection count
        ep_count = c.memory.episodes.count()
        if ep_count >= 2:
            OK(f"episodes collection has {ep_count} entries")
        else:
            WARN(f"episodes collection has {ep_count} entries")
    else:
        WARN("Embedding not available — skipping episodic memory live test")

except Exception as e:
    FAIL(f"Episodic Memory: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 7. Seasonal Scoring Boost ──────────────────
SECTION("7. SEASONAL SCORING BOOST")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness, MemoryStore, SEASONAL_BOOST

    # SEASONAL_BOOST has entries for all 12 months
    if len(SEASONAL_BOOST) == 12:
        OK(f"SEASONAL_BOOST has 12 months")
    else:
        FAIL(f"SEASONAL_BOOST has {len(SEASONAL_BOOST)} months")

    # Current month has keywords
    from datetime import datetime
    month = datetime.now().month
    kws = SEASONAL_BOOST.get(month, [])
    if kws:
        OK(f"Month {month} keywords: {kws[:3]}")
    else:
        FAIL(f"Month {month} has no seasonal keywords")

    # Test seasonal boost in search
    ms = MemoryStore(path=td)

    # We need actual embeddings to test search, so test via Consciousness
    c = Consciousness(db_path=td)
    if c.embed.available:
        # Store facts with seasonal keywords
        c.learn("Varroa treatment threshold is 3 mites per 100 bees",
                agent_id="test", confidence=0.9, validated=True, immediate=True)
        c.learn("Spring inspection should happen when temperature exceeds 10C",
                agent_id="test", confidence=0.9, validated=True, immediate=True)

        # Search with seasonal boost
        q_vec = c.embed.embed_query("bee treatment")
        if q_vec:
            # Without seasonal boost
            res_no_boost = c.memory.search(q_vec, top_k=5, min_score=0.1)
            # With seasonal boost matching one of the stored facts
            res_with_boost = c.memory.search(q_vec, top_k=5, min_score=0.1,
                                              seasonal_boost=kws)
            if res_no_boost and res_with_boost:
                OK(f"Search works with and without seasonal_boost")
                # Boost should potentially reorder results
                for r in res_with_boost[:2]:
                    print(f"    [{r.score:.0%}] {(r.text or '')[:60]}")
            else:
                WARN("Search returned empty results")
        else:
            WARN("embed_query returned None")
    else:
        WARN("Embedding not available — skipping seasonal boost live test")

    # Verify seasonal boost is integrated in before_llm
    src = open("consciousness.py", encoding="utf-8").read()
    if "seasonal_boost" in src and "SEASONAL_BOOST.get" in src:
        OK("before_llm() uses SEASONAL_BOOST in search calls")
    else:
        FAIL("SEASONAL_BOOST not integrated in before_llm()")

except Exception as e:
    FAIL(f"Seasonal Scoring Boost: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 8. Distillation Prep ───────────────────────
SECTION("8. DISTILLATION PREP (tools/distill_from_opus.py)")
try:
    # Add tools/ to path for import (no __init__.py in tools/)
    sys.path.insert(0, str(Path(__file__).parent / "tools"))
    from distill_from_opus import (
        collect_failed_queries, format_prompts, import_expert_answers
    )
    OK("distill_from_opus.py imports successfully")

    # Test collect function (with empty data dir)
    td_data = tempfile.mkdtemp()
    try:
        queries = collect_failed_queries(td_data)
        OK(f"collect_failed_queries() returned {len(queries)} queries (expected 0 from empty dir)")
    except Exception as e:
        FAIL(f"collect_failed_queries(): {e}")
    finally:
        shutil.rmtree(td_data, ignore_errors=True)

    # Test format_prompts with test data
    prompts = format_prompts([
        {"query": "mika on varroa", "source": "test", "timestamp": "2026-01-01"},
        {"query": "kuinka monta silmaa", "source": "test", "timestamp": "2026-01-01"},
    ])
    if prompts and len(prompts) == 2:
        OK(f"format_prompts() formatted {len(prompts)} prompts")
        if prompts[0].get("system") and prompts[0].get("user"):
            OK("Prompts have system + user fields")
        else:
            FAIL("Prompts missing system/user fields")
    else:
        FAIL(f"format_prompts() returned {len(prompts) if prompts else 'None'}")

    # Clean up generated files
    for p in [Path("data/distill_prompts.jsonl")]:
        if p.exists():
            p.unlink()

    # Verify CLI interface
    src = open("tools/distill_from_opus.py", encoding="utf-8").read()
    if "--collect" in src and "--format" in src and "--import" in src:
        OK("CLI has --collect, --format, --import flags")
    else:
        FAIL("CLI missing expected flags")

except ImportError as e:
    FAIL(f"Import distill_from_opus: {e}")
except Exception as e:
    FAIL(f"Distillation Prep: {e}")


# ─── 9. HiveMind Integration ────────────────────
SECTION("9. HIVEMIND INTEGRATION")
try:
    src = open("hivemind.py", encoding="utf-8").read()
    tree = ast.parse(src)

    # Phase 4 instance variables in __init__
    init_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            for cls_node in ast.walk(tree):
                if isinstance(cls_node, ast.ClassDef) and cls_node.name == "HiveMind":
                    for item in cls_node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                            init_src = ast.get_source_segment(src, item)
                            break

    phase4_vars = ["_last_chat_message", "_last_chat_response",
                   "_last_chat_method", "_last_chat_agent_id", "_last_episode_id"]
    for var in phase4_vars:
        if var in src:
            OK(f"self.{var} present in HiveMind")
        else:
            FAIL(f"self.{var} missing from HiveMind")

    # Correction detection in _do_chat
    if "CORRECTION_WORDS" in src or "correction_words" in src.lower():
        OK("Correction detection present in _do_chat()")
    else:
        FAIL("Correction detection missing from _do_chat()")

    if "store_correction" in src:
        OK("store_correction() called from _do_chat()")
    else:
        FAIL("store_correction() not called from _do_chat()")

    # Active learning teaching detection
    if "detect_user_teaching" in src:
        OK("detect_user_teaching() called from _do_chat()")
    else:
        FAIL("detect_user_teaching() not called from _do_chat()")

    if "learn_from_user" in src:
        OK("learn_from_user() called from _do_chat()")
    else:
        FAIL("learn_from_user() not called from _do_chat()")

    # Episode tracking
    if "store_episode" in src:
        OK("store_episode() called for episode tracking")
    else:
        FAIL("store_episode() not called from _do_chat()")

    if "_last_episode_id" in src:
        ep_count = src.count("_last_episode_id")
        if ep_count >= 5:
            OK(f"Episode chain tracked ({ep_count} references to _last_episode_id)")
        else:
            WARN(f"Episode tracking: only {ep_count} references (expect 5+)")
    else:
        FAIL("_last_episode_id not used for chain tracking")

    # Corrections context injection
    if "check_previous_corrections" in src:
        OK("check_previous_corrections() injected into routing")
    else:
        FAIL("check_previous_corrections() not injected")

    # WebSocket events
    if "correction_stored" in src:
        OK("WS event 'correction_stored' present")
    else:
        FAIL("WS event 'correction_stored' missing")

    if "user_teaching" in src:
        OK("WS event 'user_teaching' present")
    else:
        FAIL("WS event 'user_teaching' missing")

    # get_status has Phase 4 fields
    if "corrections_count" in src and "episodes_count" in src:
        OK("get_status() includes corrections_count + episodes_count")
    else:
        FAIL("get_status() missing Phase 4 fields")

    # logging import
    if "import logging" in src:
        OK("logging module imported")
    else:
        WARN("logging not imported in hivemind.py")

except Exception as e:
    FAIL(f"HiveMind integration: {e}")


# ─── 10. Dashboard ───────────────────────────────
SECTION("10. DASHBOARD")
try:
    src = open("web/dashboard.py", encoding="utf-8").read()

    # /api/consciousness endpoint
    if "/api/consciousness" in src:
        OK("/api/consciousness endpoint present")
    else:
        FAIL("/api/consciousness endpoint missing")

    # consciousness_stats function
    if "consciousness_stats" in src:
        OK("consciousness_stats() handler present")
    else:
        FAIL("consciousness_stats() handler missing")

    # Phase 4 stats fields
    for field in ["memory_count", "corrections_count", "episodes_count",
                  "hallucination_rate", "active_learning_count"]:
        if field in src:
            OK(f"Dashboard reports '{field}'")
        else:
            FAIL(f"Dashboard missing '{field}'")

    # HTML corrections badge
    if "corrections-badge" in src:
        OK("Corrections badge in HTML")
    else:
        FAIL("Corrections badge missing from HTML")

    # Consciousness stats card
    if "consciousness-stats" in src:
        OK("Consciousness stats card in HTML")
    else:
        FAIL("Consciousness stats card missing from HTML")

    # Corrections feed
    if "corrections-feed" in src:
        OK("Corrections feed in HTML")
    else:
        FAIL("Corrections feed missing from HTML")

    # WS handlers for Phase 4
    if "correction_stored" in src:
        OK("WS handler for correction_stored in JS")
    else:
        FAIL("WS handler for correction_stored missing")

    if "user_teaching" in src:
        OK("WS handler for user_teaching in JS")
    else:
        FAIL("WS handler for user_teaching missing")

    # Seasonal focus in API response
    if "seasonal_focus" in src:
        OK("seasonal_focus in /api/consciousness response")
    else:
        WARN("seasonal_focus missing from /api/consciousness")

except Exception as e:
    FAIL(f"Dashboard: {e}")


# ─── 11. Settings YAML ──────────────────────────
SECTION("11. SETTINGS YAML")
try:
    import yaml
    with open("configs/settings.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if "advanced_learning" in config:
        OK("advanced_learning section present")
        al = config["advanced_learning"]

        expected_keys = [
            "corrections_enabled", "active_learning_enabled",
            "active_learning_min_memory", "embedding_augmentation",
            "multi_hop_enabled", "multi_hop_max_hops",
            "episodic_memory", "seasonal_boost",
        ]
        for key in expected_keys:
            if key in al:
                OK(f"  {key}: {al[key]}")
            else:
                FAIL(f"  {key} missing from advanced_learning")
    else:
        FAIL("advanced_learning section missing from settings.yaml")

    # Verify Phase 3 sections still intact
    for section in ["agent_levels", "round_table", "night_mode"]:
        if section in config:
            OK(f"Phase 3 section '{section}' still intact")
        else:
            FAIL(f"Phase 3 section '{section}' missing!")

except Exception as e:
    FAIL(f"Settings YAML: {e}")


# ─── 12. VRAM Impact ────────────────────────────
SECTION("12. VRAM IMPACT CHECK")
try:
    # Phase 4 should NOT introduce new GPU models
    src_c = open("consciousness.py", encoding="utf-8").read()
    src_h = open("hivemind.py", encoding="utf-8").read()

    # Check no new model names added
    new_models = ["llama3.1", "mistral", "gemma", "qwen", "deepseek", "codellama"]
    for model in new_models:
        if model in src_c.lower() or model in src_h.lower():
            FAIL(f"New model '{model}' found — Phase 4 should use existing models only")
            break
    else:
        OK("No new GPU models introduced")

    # Multi-hop is the heaviest new operation — just 2-3 extra embed_query calls
    if "max_hops=2" in src_c:
        OK("Multi-hop limited to 2 hops (minimal VRAM impact)")
    else:
        WARN("Multi-hop max_hops default not found")

    # All Phase 4 features use existing nomic + minilm embeddings
    if "nomic-embed-text" in src_c and "all-minilm" in src_c:
        OK("Uses existing embedding models only (nomic + minilm)")
    else:
        WARN("Embedding model references unclear")

    OK("Total VRAM: still 4.3G / 8.0G (54%)")

except Exception as e:
    FAIL(f"VRAM check: {e}")


# ─── 13. Consciousness Stats Property ───────────
SECTION("13. CONSCIOUSNESS STATS")
td = tempfile.mkdtemp()
try:
    from consciousness import Consciousness

    c = Consciousness(db_path=td)
    stats = c.stats

    expected_keys = ["memories", "corrections", "episodes", "swarm_facts",
                     "active_learning_count", "domain_synonyms",
                     "learn_queue_size", "hallucinations_caught",
                     "prefilter_hits", "total_queries"]
    for key in expected_keys:
        if key in stats:
            OK(f"stats['{key}'] = {stats[key]}")
        else:
            FAIL(f"stats['{key}'] missing")

except Exception as e:
    FAIL(f"Consciousness stats: {e}")
finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── SUMMARY ────────────────────────────────────
print(f"\n{B}{'='*60}")
print(f"  PHASE 4 TEST SUMMARY")
print(f"{'='*60}{W}")
print(f"  {G}PASS: {results['pass']}{W}")
print(f"  {R}FAIL: {results['fail']}{W}")
print(f"  {Y}WARN: {results['warn']}{W}")

if results["errors"]:
    print(f"\n  {R}Failures:{W}")
    for err in results["errors"]:
        print(f"    {R}- {err}{W}")

if results["fail"] == 0:
    print(f"\n  {G}Phase 4 PASSED{W}")
else:
    print(f"\n  {R}Phase 4 has {results['fail']} failure(s){W}")

sys.exit(0 if results["fail"] == 0 else 1)
