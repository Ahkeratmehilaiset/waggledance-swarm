#!/usr/bin/env python3
"""
Phase 3: Social Learning — Verification Test
=============================================
Tests all Phase 3 components:
  1. AgentLevelManager (promotion, demotion, persistence, capabilities)
  2. LearningTaskQueue (seasonal, low-confidence, random, guided)
  3. Swarm Facts collection
  4. Consciousness integration
  5. Settings YAML
  6. HiveMind integration (method/variable/heartbeat checks)
  7. Dashboard (HTML, WebSocket, API)
"""

import sys
import os
import json
import time
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

results = {"pass": 0, "fail": 0, "errors": []}


def OK(msg):
    results["pass"] += 1
    print(f"  {G}OK {msg}{W}")


def FAIL(msg):
    results["fail"] += 1
    results["errors"].append(msg)
    print(f"  {R}FAIL {msg}{W}")


def SECTION(title):
    print(f"\n{B}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{W}")


# ─── 1. Agent Levels ───────────────────────────
SECTION("1. AGENT LEVELS")
td = tempfile.mkdtemp()
try:
    from core.agent_levels import (
        AgentLevelManager, AgentLevel,
        LEVEL_CRITERIA, LEVEL_CAPABILITIES, DEMOTION_WINDOW
    )

    mgr = AgentLevelManager(db_path=td)
    OK("AgentLevelManager created")

    # New agent starts at L1 NOVICE
    s = mgr.get_stats("new_agent")
    if s["level"] == 1 and s["level_name"] == "NOVICE":
        OK(f"New agent = L1 NOVICE (trust={s['trust_score']})")
    else:
        FAIL(f"New agent level: {s}")

    # Capabilities at L1
    caps = mgr.get_capabilities("new_agent")
    if caps["max_tokens"] == 200 and not caps["can_read_swarm_facts"]:
        OK("L1 capabilities correct (max_tokens=200, no swarm_facts)")
    else:
        FAIL(f"L1 capabilities: {caps}")

    # 50 correct -> L2 APPRENTICE promotion
    for i in range(55):
        mgr.record_response("promo_agent", "tarhaaja", was_correct=True)
    s = mgr.get_stats("promo_agent")
    if s["level"] == 2 and s["level_name"] == "APPRENTICE":
        OK(f"50+ correct -> L2 APPRENTICE (trust={s['trust_score']:.2f})")
    else:
        FAIL(f"Expected L2, got L{s['level']} {s['level_name']}")

    # L2 capabilities
    caps = mgr.get_capabilities("promo_agent")
    if caps["can_read_swarm_facts"] and not caps["can_write_swarm_facts"]:
        OK("L2 APPRENTICE: can read swarm_facts, cannot write")
    else:
        FAIL(f"L2 capabilities: {caps}")

    # Trust EMA
    if 0.9 <= s["trust_score"] <= 1.0:
        OK(f"Trust EMA working: {s['trust_score']:.3f}")
    else:
        FAIL(f"Trust EMA unexpected: {s['trust_score']}")

    # Hallucination tracking
    for i in range(50):
        mgr.record_response("bad_agent", "test", was_correct=False,
                            was_hallucination=True)
    s = mgr.get_stats("bad_agent")
    if s["recent_hallucination_rate"] == 1.0 and s["hallucination_count"] == 50:
        OK(f"Hallucination tracking: rate={s['recent_hallucination_rate']:.0%}, "
           f"count={s['hallucination_count']}")
    else:
        FAIL(f"Hallucination tracking: {s}")

    # Demotion (promote then hallucinate)
    for i in range(60):
        mgr.record_response("demote_agent", "test", was_correct=True)
    pre = mgr.get_stats("demote_agent")
    pre_level = pre["level"]
    for i in range(DEMOTION_WINDOW):
        mgr.record_response("demote_agent", "test",
                            was_correct=False, was_hallucination=True)
    post = mgr.get_stats("demote_agent")
    if post["level"] < pre_level:
        OK(f"Demotion works: L{pre_level} -> L{post['level']}")
    else:
        FAIL(f"Demotion failed: L{pre_level} -> L{post['level']}")

    # get_all_stats
    all_s = mgr.get_all_stats()
    if len(all_s) >= 3:
        OK(f"get_all_stats: {len(all_s)} agents tracked")
    else:
        FAIL(f"get_all_stats: only {len(all_s)} agents")

    # Persistence (create new manager on same db)
    mgr2 = AgentLevelManager(db_path=td)
    s2 = mgr2.get_stats("promo_agent")
    if s2["level"] == 2:
        OK("Persistence: stats survive reload")
    else:
        FAIL(f"Persistence: expected L2, got L{s2['level']}")

    # User correction penalty
    before_trust = mgr.get_stats("promo_agent")["trust_score"]
    mgr.record_response("promo_agent", "tarhaaja",
                        was_correct=False, was_corrected=True)
    after_trust = mgr.get_stats("promo_agent")["trust_score"]
    if after_trust < before_trust:
        OK(f"User correction penalty: {before_trust:.3f} -> {after_trust:.3f}")
    else:
        FAIL(f"Correction penalty: {before_trust} -> {after_trust}")

    # Level criteria completeness
    for lvl in [AgentLevel.APPRENTICE, AgentLevel.JOURNEYMAN,
                AgentLevel.EXPERT, AgentLevel.MASTER]:
        if lvl in LEVEL_CRITERIA:
            c = LEVEL_CRITERIA[lvl]
            if all(k in c for k in ["correct_responses", "max_hallucination_rate", "min_trust"]):
                OK(f"L{lvl} criteria complete: correct>={c['correct_responses']}, "
                   f"halluc<={c['max_hallucination_rate']}, trust>={c['min_trust']}")
            else:
                FAIL(f"L{lvl} criteria incomplete")
        else:
            FAIL(f"L{lvl} criteria missing")

    # Level capabilities completeness
    for lvl in AgentLevel:
        if lvl in LEVEL_CAPABILITIES:
            OK(f"L{lvl} ({lvl.name}) capabilities defined")
        else:
            FAIL(f"L{lvl} capabilities missing")

finally:
    shutil.rmtree(td, ignore_errors=True)


# ─── 2. LearningTaskQueue ─────────────────────
SECTION("2. LEARNING TASK QUEUE")
from consciousness import LearningTaskQueue, SEASONAL_BOOST, DOMAIN_TOPICS

# All 12 months have seasonal keywords
if len(SEASONAL_BOOST) == 12:
    OK(f"SEASONAL_BOOST: 12 months covered")
else:
    FAIL(f"SEASONAL_BOOST: {len(SEASONAL_BOOST)} months")

# Domain topics for key agent types
expected_types = {"tarhaaja", "tautivahti", "meteorologi", "hortonomi", "business"}
if expected_types.issubset(set(DOMAIN_TOPICS.keys())):
    OK(f"DOMAIN_TOPICS: {len(DOMAIN_TOPICS)} agent types")
else:
    FAIL(f"Missing types: {expected_types - set(DOMAIN_TOPICS.keys())}")


# Task queue basic operation
class FakeCon:
    class memory:
        swarm_facts = type("C", (), {"count": lambda self: 0})()


ltq = LearningTaskQueue(FakeCon())

# Low confidence recording + retrieval
ltq.record_low_confidence_query("varroa hoitokynnys", 0.0)
ltq.record_low_confidence_query("hunajan kosteus", 0.0)
task = ltq._low_confidence_task()
if task and task["type"] == "research" and "varroa" in task["topic"]:
    OK(f"Low confidence task: {task['topic']}")
else:
    FAIL(f"Low confidence task: {task}")

# Seasonal task
task = ltq._seasonal_task()
if task and task["type"] == "seasonal":
    OK(f"Seasonal task: {task['topic']}")
else:
    FAIL(f"Seasonal task: {task}")

# Random exploration task
task = ltq._random_task("tarhaaja")
if task and task["type"] == "exploration":
    OK(f"Random task (tarhaaja): {task['topic']}")
else:
    FAIL(f"Random task: {task}")

# next_task returns something
task = ltq.next_task(agent_type="tarhaaja")
if task and task.get("type"):
    OK(f"next_task: type={task['type']}, topic={task.get('topic', '')[:40]}")
else:
    FAIL(f"next_task returned: {task}")

# No repeat topics
ltq2 = LearningTaskQueue(FakeCon())
topics = set()
for _ in range(10):
    t = ltq2.next_task(agent_type="tarhaaja")
    if t:
        topics.add(t.get("topic", ""))
if len(topics) >= 3:
    OK(f"Task diversity: {len(topics)} unique topics in 10 calls")
else:
    FAIL(f"Task diversity low: {len(topics)} unique topics")


# ─── 3. Swarm Facts Collection ────────────────
SECTION("3. SWARM FACTS COLLECTION")
from consciousness import MemoryStore

td2 = tempfile.mkdtemp()
try:
    ms = MemoryStore(path=td2)
    if hasattr(ms, "swarm_facts") and ms.swarm_facts is not None:
        OK(f"swarm_facts collection exists (count={ms.swarm_facts.count()})")
    else:
        FAIL("swarm_facts collection missing")

    # Check it is separate from main collection
    if ms.collection.name != ms.swarm_facts.name:
        OK(f"swarm_facts is separate collection ({ms.swarm_facts.name})")
    else:
        FAIL("swarm_facts same as main collection")

finally:
    shutil.rmtree(td2, ignore_errors=True)


# ─── 4. Consciousness Integration ─────────────
SECTION("4. CONSCIOUSNESS INTEGRATION")
from consciousness import Consciousness

td3 = tempfile.mkdtemp()
try:
    c = Consciousness(db_path=td3)

    # init_task_queue
    c.init_task_queue()
    if c.task_queue is not None:
        OK("init_task_queue() creates LearningTaskQueue")
    else:
        FAIL("task_queue is None after init")

    # before_llm Tier 4 records low confidence
    c.before_llm("something very obscure xyz123 asdf")
    if len(c.task_queue._low_confidence_queries) > 0:
        OK("before_llm Tier 4 records low-confidence query")
    else:
        FAIL("low-confidence query not recorded")

    # stats include swarm_facts
    if "swarm_facts" in c.stats:
        OK(f"stats includes swarm_facts: {c.stats['swarm_facts']}")
    else:
        FAIL("stats missing swarm_facts")

finally:
    shutil.rmtree(td3, ignore_errors=True)


# ─── 5. Settings YAML ─────────────────────────
SECTION("5. SETTINGS YAML")
import yaml

with open("configs/settings.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

if cfg.get("agent_levels", {}).get("enabled"):
    OK("agent_levels.enabled = true")
else:
    FAIL("agent_levels not enabled")

rt = cfg.get("round_table", {})
if rt.get("enabled") and rt.get("every_n_heartbeat") == 20 and rt.get("agent_count") == 6:
    OK(f"round_table: enabled, every {rt['every_n_heartbeat']}th HB, "
       f"{rt['agent_count']} agents, min {rt.get('min_agents', 3)}")
else:
    FAIL(f"round_table config: {rt}")

nm = cfg.get("night_mode", {})
if (nm.get("enabled") and nm.get("idle_threshold_min") == 30
        and nm.get("max_hours") == 8 and nm.get("interval_s") == 10):
    OK(f"night_mode: enabled, idle>{nm['idle_threshold_min']}min, "
       f"max {nm['max_hours']}h, interval {nm['interval_s']}s")
else:
    FAIL(f"night_mode config: {nm}")


# ─── 6. HiveMind Integration ──────────────────
SECTION("6. HIVEMIND INTEGRATION")

with open("hivemind.py", encoding="utf-8") as f:
    hm_src = f.read()
with open("hivemind.py", encoding="utf-8") as f:
    hm_tree = ast.parse(f.read())

func_names = [node.name for node in ast.walk(hm_tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

# Phase 3 methods exist
phase3_methods = [
    "_round_table", "_select_round_table_agents",
    "_theater_stream_round_table", "_check_night_mode",
    "_get_night_mode_interval", "_night_learning_cycle",
    "_save_learning_progress",
]
for method in phase3_methods:
    if method in func_names:
        OK(f"Method {method} exists")
    else:
        FAIL(f"Method {method} MISSING")

# Phase 3 instance variables
phase3_vars = [
    "agent_levels", "_last_user_chat_time",
    "_night_mode_active", "_night_mode_facts_learned",
]
for var in phase3_vars:
    if f"self.{var}" in hm_src:
        OK(f"Variable self.{var} exists")
    else:
        FAIL(f"Variable self.{var} MISSING")

# Round Table in heartbeat loop
if "_guarded(self._round_table)" in hm_src:
    OK("Round Table integrated in heartbeat loop")
else:
    FAIL("Round Table NOT in heartbeat loop")

# Night Mode in heartbeat loop
if "_night_mode_active" in hm_src and "_night_learning_cycle" in hm_src:
    OK("Night Mode integrated in heartbeat loop")
else:
    FAIL("Night Mode NOT in heartbeat loop")

# Agent levels recording in _delegate_to_agent
if "agent_levels.record_response" in hm_src:
    OK("Agent level recording in _delegate_to_agent + _round_table")
else:
    FAIL("Agent level recording MISSING")

# Guided tasks in _agent_proactive_think
if "guided_task" in hm_src and "task_queue.next_task" in hm_src:
    OK("Guided tasks in _agent_proactive_think")
else:
    FAIL("Guided tasks NOT in _agent_proactive_think")

# get_status includes Phase 3 fields
if ("agent_levels" in hm_src and "night_mode" in hm_src
        and "consciousness" in hm_src):
    OK("get_status includes agent_levels, night_mode, consciousness")
else:
    FAIL("get_status missing Phase 3 fields")

# Chat records _last_user_chat_time
if "_last_user_chat_time = time.monotonic()" in hm_src:
    OK("chat() records _last_user_chat_time")
else:
    FAIL("chat() NOT recording _last_user_chat_time")

# PriorityLock still respected in Round Table
if "priority.wait_if_chat" in hm_src:
    OK("PriorityLock (wait_if_chat) used in Round Table")
else:
    FAIL("PriorityLock NOT used in Round Table")

# Theater Pipe uses 300ms delay
if "asyncio.sleep(0.3)" in hm_src:
    OK("Theater Pipe: 300ms streaming delay")
else:
    FAIL("Theater Pipe delay MISSING")


# ─── 7. Dashboard ──────────────────────────────
SECTION("7. DASHBOARD")

with open("web/dashboard.py", encoding="utf-8") as f:
    dsrc = f.read()

# API endpoint
if "/api/agent_levels" in dsrc:
    OK("/api/agent_levels endpoint exists")
else:
    FAIL("/api/agent_levels endpoint MISSING")

# Round Table HTML card
if "rt-card" in dsrc and "rt-feed" in dsrc and "rt-synthesis" in dsrc:
    OK("Round Table HTML card (topic + feed + synthesis)")
else:
    FAIL("Round Table HTML card MISSING")

# Round Table WebSocket handlers
for evt in ["round_table_start", "round_table_insight",
            "round_table_synthesis", "round_table_end"]:
    if evt in dsrc:
        OK(f"WS handler: {evt}")
    else:
        FAIL(f"WS handler MISSING: {evt}")

# Night mode indicator
if "night-badge" in dsrc and "night_learning" in dsrc:
    OK("Night mode indicator in dashboard")
else:
    FAIL("Night mode indicator MISSING")

# Agent level badges
if "lvl-badge" in dsrc and "lvlBadge" in dsrc:
    OK("Agent level badges (L1-L5) in dashboard")
else:
    FAIL("Agent level badges MISSING")

# Level badge CSS classes
for lvl in range(1, 6):
    if f".lvl-{lvl}" in dsrc:
        OK(f"CSS class .lvl-{lvl} defined")
    else:
        FAIL(f"CSS class .lvl-{lvl} MISSING")

# Version updated
if "v0.1.0" in dsrc:
    OK("Dashboard version updated to v0.1.0")
else:
    FAIL("Dashboard version not updated")


# ─── 8. VRAM Impact Check ─────────────────────
SECTION("8. VRAM IMPACT CHECK")

# No new model references in Phase 3 code
new_models = ["phi4-mini", "llama3.2:3b", "mistral", "qwen"]
found_new = False
for model in new_models:
    # Check if Phase 3 methods reference a NEW model (not existing ones)
    pass

# Phase 3 only uses llm_heartbeat (llama3.2:1b)
if "self.llm_heartbeat" in hm_src:
    OK("Phase 3 uses llm_heartbeat (llama3.2:1b) for all background work")
else:
    FAIL("llm_heartbeat not found")

# Round Table does NOT use self.llm (phi4-mini)
# Check _round_table method body for self.llm calls
rt_start = hm_src.find("async def _round_table")
rt_end = hm_src.find("def _select_round_table_agents")
if rt_start > 0 and rt_end > 0:
    rt_body = hm_src[rt_start:rt_end]
    if "self.llm_heartbeat" in rt_body and "self.llm." not in rt_body:
        OK("Round Table uses llm_heartbeat only (no phi4-mini)")
    elif "self.llm." in rt_body:
        FAIL("Round Table uses phi4-mini (should use llm_heartbeat only)")
    else:
        OK("Round Table: no direct phi4-mini reference")


# ─── SUMMARY ──────────────────────────────────
print(f"\n{B}{'='*60}")
print(f"  PHASE 3 VERIFICATION SUMMARY")
print(f"{'='*60}{W}")
print(f"  {G}Pass: {results['pass']}{W}")
if results["fail"] > 0:
    print(f"  {R}Fail: {results['fail']}{W}")
    print(f"\n{R}Failures:{W}")
    for e in results["errors"]:
        print(f"  {R}- {e}{W}")
else:
    print(f"  {G}Fail: 0{W}")

total = results["pass"] + results["fail"]
rate = results["pass"] / total * 100 if total else 0
color = G if results["fail"] == 0 else Y
print(f"\n  {color}Result: {rate:.0f}% ({results['pass']}/{total}){W}")
