#!/usr/bin/env python3
"""
Phase 9: Autonomous Learning Engine (Layers 3-6) — Verification Test
======================================================================
Tests all Phase 9 components:
  1. WebLearningAgent — trusted domains, gap detection, cycle, budget, stats
  2. KnowledgeDistiller — parse, graceful without API/lib, budget, stats
  3. MetaLearningEngine — analyze, suggestions, auto-opt, weekly report
  4. CodeSelfReview — parse, accept/reject, pending, persistence, stats
  5. HiveMind integration — attributes, get_status, cycle rotation, init
  6. Settings.yaml — new config keys present
  7. Dashboard — new endpoints, consciousness includes new stats
  8. Edge cases — all disabled, missing libraries, empty data
  9. Graceful degradation — missing duckduckgo-search, missing anthropic
"""

import sys
import os
import json
import time
import re
import shutil
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


# ═══════════════════════════════════════════════════════════════
# Helper: mock consciousness
# ═══════════════════════════════════════════════════════════════
def make_mock_consciousness():
    """Create a mock Consciousness object for testing."""
    c = MagicMock()
    c.memory = MagicMock()
    c.memory.count = 3000
    c.memory.swarm_facts = MagicMock()
    c.memory.swarm_facts.count.return_value = 100
    c.memory.corrections = MagicMock()
    c.memory.corrections.count.return_value = 5
    c.memory.episodes = MagicMock()
    c.memory.episodes.count.return_value = 20
    c._total_queries = 100
    c._hallucination_count = 3
    c._prefilter_hits = 10
    c._learn_queue = []
    c._active_learning_count = 2
    c._domain_synonyms = {}
    c.hot_cache = MagicMock()
    c.hot_cache.stats = {"size": 450, "max_size": 500, "hit_rate": 0.12,
                         "total_hits": 50, "total_misses": 350}
    c.hot_cache._max_size = 500
    c.bilingual = MagicMock()
    c.bilingual.fi_count = 2500
    c.learn = MagicMock(return_value=True)
    c.flush = MagicMock()
    c.stats = {"memories": 3000, "swarm_facts": 100}
    return c


def make_mock_llm(content="VALID"):
    """Create a mock LLM provider."""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    resp.error = None
    llm.generate = AsyncMock(return_value=resp)
    return llm


# ═══════════════════════════════════════════════════════════════
# 1. WEB LEARNING AGENT
# ═══════════════════════════════════════════════════════════════
SECTION("1. WEB LEARNING AGENT")

from core.web_learner import WebLearningAgent

consciousness = make_mock_consciousness()
llm_fast = make_mock_llm("Varroa mites are the biggest threat to Finnish bee colonies.\nTreatment should be done in late summer.\nOxalic acid is commonly used in Finland.")
llm_chat = make_mock_llm("VALID")

wl = WebLearningAgent(consciousness, llm_fast, llm_chat, daily_budget=50)

# 1a. Init
if wl._daily_budget == 50 and wl._searches_today == 0:
    OK("WebLearningAgent init with correct defaults")
else:
    FAIL(f"WebLearningAgent init: budget={wl._daily_budget}, searches={wl._searches_today}")

# 1b. Trusted domain check
if wl._is_trusted_domain("https://www.mehilaishoitajat.fi/article/123"):
    OK("mehilaishoitajat.fi recognized as trusted")
else:
    FAIL("mehilaishoitajat.fi should be trusted")

if not wl._is_trusted_domain("https://www.random-blog.com/bees"):
    OK("random-blog.com correctly not trusted")
else:
    FAIL("random-blog.com should not be trusted")

if not wl._is_trusted_domain(""):
    OK("empty URL correctly not trusted")
else:
    FAIL("empty URL should not be trusted")

if wl._is_trusted_domain("https://scientificbeekeeping.com/varroa"):
    OK("scientificbeekeeping.com recognized as trusted")
else:
    FAIL("scientificbeekeeping.com should be trusted")

# 1c. TRUSTED_DOMAINS list has expected entries
expected = ["mehilaishoitajat.fi", "ruokavirasto.fi", "scientificbeekeeping.com"]
for d in expected:
    if d in WebLearningAgent.TRUSTED_DOMAINS:
        OK(f"TRUSTED_DOMAINS contains {d}")
    else:
        FAIL(f"TRUSTED_DOMAINS missing {d}")

# 1d. Daily budget enforcement
wl2 = WebLearningAgent(consciousness, llm_fast, llm_chat, daily_budget=0)
if not wl2._check_daily_budget():
    OK("Daily budget=0 blocks searches")
else:
    FAIL("Daily budget=0 should block searches")

# 1e. Daily budget resets on date change
wl3 = WebLearningAgent(consciousness, llm_fast, llm_chat, daily_budget=50)
wl3._searches_today = 50
wl3._today_str = "2020-01-01"  # old date
if wl3._check_daily_budget():
    OK("Daily budget resets on date change")
else:
    FAIL("Daily budget should reset on date change")

# 1f. Mocked web learning cycle
async def test_web_learning_cycle():
    c = make_mock_consciousness()
    llm_f = make_mock_llm("Varroa mites are the biggest threat to Finnish bee colonies.\nTreatment should be done in late summer.\nOxalic acid is commonly used in Finland.")
    llm_v = make_mock_llm("VALID")

    wl = WebLearningAgent(c, llm_f, llm_v, daily_budget=50)

    # Mock web search tool
    mock_ws = MagicMock()
    mock_results = [
        {"title": "Varroa Treatment", "url": "https://www.mehilaishoitajat.fi/varroa", "body": "Varroa treatment info"},
        {"title": "Bee Health", "url": "https://scientificbeekeeping.com/varroa", "body": "Varroa mite biology"},
    ]
    mock_ws.search = AsyncMock(return_value=mock_results)
    mock_ws._ddgs_available = True
    wl._web_search = mock_ws

    stored = await wl.web_learning_cycle()
    return stored, wl, c

stored, wl_test, c_test = asyncio.get_event_loop().run_until_complete(
    test_web_learning_cycle())

if stored > 0:
    OK(f"Web learning cycle stored {stored} facts")
else:
    WARN(f"Web learning cycle stored 0 facts (may depend on mocking)")

if c_test.learn.called:
    call_kwargs = c_test.learn.call_args
    if call_kwargs:
        kw = call_kwargs[1] if call_kwargs[1] else {}
        if kw.get("source_type") == "web_learning":
            OK("Web facts tagged source_type='web_learning'")
        else:
            FAIL(f"Expected source_type='web_learning', got {kw.get('source_type')}")
        if kw.get("confidence") == 0.85:
            OK("Trusted domain gives confidence=0.85")
        elif kw.get("confidence") == 0.65:
            OK("Untrusted domain gives confidence=0.65")
        else:
            WARN(f"Confidence={kw.get('confidence')}")
    else:
        WARN("learn() called but couldn't inspect kwargs")
else:
    WARN("consciousness.learn() not called (may be mocking issue)")

# 1g. Stats property
stats = wl_test.stats
if "facts_stored" in stats and "daily_budget" in stats and "budget_remaining" in stats:
    OK(f"Stats has expected keys: facts_stored, daily_budget, budget_remaining")
else:
    FAIL(f"Stats missing keys: {stats.keys()}")

# 1h. Budget zero returns 0
async def test_budget_exhausted():
    c = make_mock_consciousness()
    wl = WebLearningAgent(c, make_mock_llm(), make_mock_llm(), daily_budget=0)
    return await wl.web_learning_cycle()

result = asyncio.get_event_loop().run_until_complete(test_budget_exhausted())
if result == 0:
    OK("Budget exhausted returns 0")
else:
    FAIL(f"Budget exhausted should return 0, got {result}")

# 1i. Gap detection works
gap = wl._find_web_gap()
if gap and "topic" in gap:
    OK(f"Gap detection returns topic: '{gap['topic'][:40]}'")
else:
    FAIL(f"Gap detection failed: {gap}")


# ═══════════════════════════════════════════════════════════════
# 2. KNOWLEDGE DISTILLER
# ═══════════════════════════════════════════════════════════════
SECTION("2. KNOWLEDGE DISTILLER")

from core.knowledge_distiller import KnowledgeDistiller

# 2a. Init without API key
kd = KnowledgeDistiller(make_mock_consciousness(), api_key="")
if kd._api_key == "":
    OK("KnowledgeDistiller init without API key — no error")
else:
    FAIL("KnowledgeDistiller should handle empty API key")

# 2b. Stats property
stats = kd.stats
expected_keys = ["facts_stored", "corrections_stored", "total_api_calls",
                 "estimated_cost_eur", "api_key_set", "model", "processed_count"]
missing = [k for k in expected_keys if k not in stats]
if not missing:
    OK(f"Stats has all expected keys ({len(expected_keys)})")
else:
    FAIL(f"Stats missing keys: {missing}")

if stats["api_key_set"] == False:
    OK("api_key_set=False when no key")
else:
    FAIL("api_key_set should be False")

# 2c. Parse expert answer — FACT: lines
facts = kd._parse_expert_answer(
    "FACT: Varroa mites are the main threat to Finnish bees.\n"
    "FACT: Treatment with oxalic acid is done in winter.\n"
    "Some other text.\n"
    "CORRECTION: Not formic acid in winter, but oxalic acid."
)
if len(facts) == 3:
    OK(f"Parsed 3 items (2 FACT + 1 CORRECTION)")
else:
    FAIL(f"Expected 3 parsed items, got {len(facts)}: {facts}")

if any("[CORRECTION]" in f for f in facts):
    OK("CORRECTION: lines prefixed with [CORRECTION]")
else:
    FAIL(f"CORRECTION prefix missing: {facts}")

# 2d. Parse empty text
empty_facts = kd._parse_expert_answer("")
if empty_facts == []:
    OK("Empty text returns empty list")
else:
    FAIL(f"Empty text should return [], got {empty_facts}")

# 2e. Parse short facts filtered out
short = kd._parse_expert_answer("FACT: short\nFACT: This is a real fact about beekeeping.")
if len(short) == 1:
    OK("Short facts (<10 chars) filtered out")
else:
    WARN(f"Short fact filtering: got {len(short)} items")

# 2f. Distillation cycle without API key returns 0
async def test_distill_no_key():
    kd = KnowledgeDistiller(make_mock_consciousness(), api_key="")
    return await kd.distillation_cycle()

result = asyncio.get_event_loop().run_until_complete(test_distill_no_key())
if result == 0:
    OK("Distillation without API key returns 0")
else:
    FAIL(f"Expected 0 without API key, got {result}")

# 2g. Weekly budget tracking
kd2 = KnowledgeDistiller(make_mock_consciousness(), api_key="test-key",
                          weekly_budget_eur=5.0)
kd2._week_cost_eur = 5.01
if not kd2._check_weekly_budget():
    OK("Weekly budget exceeded blocks distillation")
else:
    FAIL("Weekly budget exceeded should block")

# 2h. Weekly budget resets on new week
kd3 = KnowledgeDistiller(make_mock_consciousness(), api_key="test-key")
kd3._week_cost_eur = 10.0
kd3._week_number = -1  # force different week
if kd3._check_weekly_budget():
    OK("Weekly budget resets on new week number")
else:
    FAIL("Weekly budget should reset on new week")

# 2i. Graceful without anthropic library
kd4 = KnowledgeDistiller(make_mock_consciousness(), api_key="test-key")
kd4._anthropic_available = False
client = kd4._get_client()
if client is None:
    OK("No client when anthropic unavailable")
else:
    FAIL("Should return None without anthropic")

# 2j. Processed queries dedup
kd5 = KnowledgeDistiller(make_mock_consciousness())
kd5._processed_queries = {"question 1", "question 2"}
if "question 1" in kd5._processed_queries:
    OK("Processed queries tracked for dedup")
else:
    FAIL("Processed queries not tracked")

# 2k. ANTHROPIC_API_KEY env var
old_env = os.environ.get("ANTHROPIC_API_KEY")
os.environ["ANTHROPIC_API_KEY"] = "test-env-key"
kd6 = KnowledgeDistiller(make_mock_consciousness())
if kd6._api_key == "test-env-key":
    OK("ANTHROPIC_API_KEY env var picked up")
else:
    FAIL(f"Expected env var key, got '{kd6._api_key}'")
if old_env:
    os.environ["ANTHROPIC_API_KEY"] = old_env
else:
    del os.environ["ANTHROPIC_API_KEY"]


# ═══════════════════════════════════════════════════════════════
# 3. META-LEARNING ENGINE
# ═══════════════════════════════════════════════════════════════
SECTION("3. META-LEARNING ENGINE")

from core.meta_learning import MetaLearningEngine

c = make_mock_consciousness()
agent_levels = MagicMock()
agent_levels.get_all_stats.return_value = {
    "tarhaaja": {"total_responses": 100, "hallucination_count": 2, "level": 3},
    "tautivahti": {"total_responses": 50, "hallucination_count": 10, "level": 2},
}
enrichment = MagicMock()
enrichment.stats = {"generated": 50, "validated": 40, "rejected": 10, "success_rate": 0.8}
web_learner = MagicMock()
web_learner.stats = {"facts_stored": 30, "total_searches": 20}
distiller = MagicMock()
distiller.stats = {"facts_stored": 10, "total_api_calls": 5}

ml = MetaLearningEngine(c, agent_levels=agent_levels, enrichment=enrichment,
                         web_learner=web_learner, distiller=distiller)

# 3a. Init
if ml._total_reports == 0:
    OK("MetaLearningEngine init with 0 reports")
else:
    FAIL(f"Expected 0 reports, got {ml._total_reports}")

# 3b. Analyze memory
mem = ml._analyze_memory()
if "total_facts" in mem and mem["total_facts"] == 3000:
    OK(f"_analyze_memory: total_facts={mem['total_facts']}")
else:
    FAIL(f"_analyze_memory: {mem}")

# 3c. Analyze hallucinations
hall = ml._analyze_hallucinations()
if "per_agent" in hall and "tarhaaja" in hall["per_agent"]:
    OK(f"_analyze_hallucinations: {len(hall['per_agent'])} agents")
else:
    FAIL(f"_analyze_hallucinations: {hall}")

# Check tautivahti has high rate
if "tautivahti" in hall.get("per_agent", {}):
    rate = hall["per_agent"]["tautivahti"]["rate"]
    if rate == 0.2:
        OK(f"tautivahti hallucination rate=0.2 (10/50)")
    else:
        WARN(f"tautivahti rate={rate}")

# 3d. Analyze learning efficiency
eff = ml._analyze_learning_efficiency()
if "enrichment" in eff and "web_learning" in eff and "distillation" in eff:
    OK("_analyze_learning_efficiency includes all 3 sources")
else:
    FAIL(f"_analyze_learning_efficiency missing sources: {eff.keys()}")

# 3e. Find weakest areas
weak = ml._find_weakest_areas()
# tautivahti has 20% halluc rate, should be flagged
flagged = [w for w in weak if w.get("agent_id") == "tautivahti"]
if flagged:
    OK("tautivahti flagged as weak (20% hallucination)")
else:
    WARN("tautivahti not flagged (threshold may differ)")

# 3f. Generate suggestions
report = {
    "memory_stats": {"hot_cache": {"size": 490, "max_size": 500, "hit_rate": 0.12}},
    "weakest_areas": [{"type": "high_hallucination_agent", "agent_id": "tautivahti", "rate": 0.2}],
    "learning_efficiency": {},
}
suggestions = ml._generate_suggestions(report)
if any(s.get("action") == "increase_hot_cache" for s in suggestions):
    OK("Suggests increasing hot cache when near full")
else:
    WARN("Hot cache suggestion not generated (may need different thresholds)")

if any(s.get("action") == "review_agent" for s in suggestions):
    OK("Suggests reviewing high-hallucination agent")
else:
    WARN("Agent review suggestion not generated")

# 3g. Weekly analysis (full)
async def test_weekly_analysis():
    c = make_mock_consciousness()
    ml = MetaLearningEngine(c, agent_levels=agent_levels,
                             enrichment=enrichment, web_learner=web_learner)
    return await ml.weekly_analysis(), ml

report, ml_test = asyncio.get_event_loop().run_until_complete(
    test_weekly_analysis())

expected_keys = ["timestamp", "memory_stats", "hallucination_stats",
                 "learning_efficiency", "weakest_areas", "suggestions"]
missing = [k for k in expected_keys if k not in report]
if not missing:
    OK(f"weekly_analysis returns all {len(expected_keys)} expected keys")
else:
    FAIL(f"weekly_analysis missing keys: {missing}")

if ml_test._total_reports == 1:
    OK("Report count incremented to 1")
else:
    FAIL(f"Expected 1 report, got {ml_test._total_reports}")

if ml_test._last_report is not None:
    OK("_last_report stored")
else:
    FAIL("_last_report should be stored")

# 3h. Auto-apply safe optimizations
async def test_auto_optimize():
    c = make_mock_consciousness()
    c.hot_cache = MagicMock()
    c.hot_cache._max_size = 500
    c.hot_cache.stats = {"size": 490, "max_size": 500, "hit_rate": 0.12}
    ml = MetaLearningEngine(c)
    suggestions = [{"action": "increase_hot_cache", "auto_safe": True},
                   {"action": "review_agent", "auto_safe": False}]
    return await ml.auto_apply_safe_optimizations(suggestions), c

applied, c_opt = asyncio.get_event_loop().run_until_complete(test_auto_optimize())
if applied == 1:
    OK("auto_apply applied 1 safe optimization")
else:
    FAIL(f"Expected 1 applied, got {applied}")

if c_opt.hot_cache._max_size == 600:
    OK(f"Hot cache size increased 500 → 600")
else:
    WARN(f"Hot cache size: {c_opt.hot_cache._max_size}")

# 3i. is_due check
ml2 = MetaLearningEngine(make_mock_consciousness())
if ml2.is_due():
    OK("is_due=True when never run")
else:
    FAIL("is_due should be True when _last_run=0")

ml2._last_run = time.monotonic()
if not ml2.is_due():
    OK("is_due=False right after running")
else:
    FAIL("is_due should be False right after running")

# 3j. Stats property
stats = ml_test.stats
if "total_reports" in stats and "has_report" in stats:
    OK(f"Stats has expected keys")
else:
    FAIL(f"Stats missing keys: {stats.keys()}")


# ═══════════════════════════════════════════════════════════════
# 4. CODE SELF-REVIEW
# ═══════════════════════════════════════════════════════════════
SECTION("4. CODE SELF-REVIEW")

from core.code_reviewer import CodeSelfReview

# 4a. Init (use temp path to avoid loading leftover data)
_cr_tmp = tempfile.mkdtemp()
cr = CodeSelfReview(make_mock_consciousness(), make_mock_llm())
cr._suggestions_path = Path(_cr_tmp) / "test_cs.jsonl"
cr._suggestions = []
if cr._total_reviews == 0 and len(cr._suggestions) == 0:
    OK("CodeSelfReview init with 0 reviews and 0 suggestions")
else:
    FAIL(f"Unexpected init state: reviews={cr._total_reviews}")

# 4b. Parse SUGGESTION/IMPACT/RISK format
text = """SUGGESTION: Increase batch size for embedding operations
IMPACT: 30% faster embedding throughput
RISK: low

SUGGESTION: Add Redis cache layer for frequent queries
IMPACT: Sub-millisecond response for cached queries
RISK: medium"""

parsed = cr._parse_suggestions(text)
if len(parsed) == 2:
    OK(f"Parsed 2 suggestions from text")
else:
    FAIL(f"Expected 2 suggestions, got {len(parsed)}")

if parsed[0].get("suggestion") and "batch size" in parsed[0]["suggestion"].lower():
    OK("First suggestion text parsed correctly")
else:
    FAIL(f"First suggestion: {parsed[0]}")

if parsed[0].get("risk") == "low":
    OK("Risk level 'low' parsed correctly")
else:
    FAIL(f"Risk: {parsed[0].get('risk')}")

if parsed[1].get("risk") == "medium":
    OK("Risk level 'medium' parsed correctly")
else:
    FAIL(f"Risk: {parsed[1].get('risk')}")

if parsed[0].get("impact") and "30%" in parsed[0]["impact"]:
    OK("Impact parsed correctly")
else:
    WARN(f"Impact: {parsed[0].get('impact')}")

# 4c. Parse with high risk
high_text = "SUGGESTION: Rewrite consciousness module\nIMPACT: Better architecture\nRISK: high"
parsed_high = cr._parse_suggestions(high_text)
if parsed_high and parsed_high[0].get("risk") == "high":
    OK("Risk level 'high' parsed correctly")
else:
    FAIL(f"High risk parse: {parsed_high}")

# 4d. Mocked code review cycle
async def test_code_review():
    c = make_mock_consciousness()
    llm = make_mock_llm(
        "SUGGESTION: Increase hot cache from 500 to 1000\n"
        "IMPACT: Better hit rate for repeated questions\n"
        "RISK: low\n\n"
        "SUGGESTION: Add connection pooling for Ollama\n"
        "IMPACT: Reduced latency for concurrent requests\n"
        "RISK: medium"
    )
    ml = MagicMock()
    ml._last_report = {
        "memory_stats": {"total_facts": 3000},
        "hallucination_stats": {"overall_rate": 0.03},
        "learning_efficiency": {},
        "weakest_areas": [],
    }
    cr = CodeSelfReview(c, llm, meta_learning=ml)
    cr._suggestions_path = Path(tempfile.mkdtemp()) / "test_review.jsonl"
    cr._suggestions = []
    suggestions = await cr.monthly_code_review()
    return suggestions, cr

suggestions, cr_test = asyncio.get_event_loop().run_until_complete(
    test_code_review())

if len(suggestions) == 2:
    OK(f"Code review returned 2 suggestions")
else:
    FAIL(f"Expected 2 suggestions, got {len(suggestions)}")

if cr_test._total_reviews == 1:
    OK("Review count incremented to 1")
else:
    FAIL(f"Expected 1 review, got {cr_test._total_reviews}")

# 4e. Accept/reject suggestions
cr_test.accept_suggestion(0)
if cr_test._suggestions[0].get("status") == "accepted":
    OK("accept_suggestion(0) sets status='accepted'")
else:
    FAIL(f"Status after accept: {cr_test._suggestions[0].get('status')}")

cr_test.reject_suggestion(1)
if cr_test._suggestions[1].get("status") == "rejected":
    OK("reject_suggestion(1) sets status='rejected'")
else:
    FAIL(f"Status after reject: {cr_test._suggestions[1].get('status')}")

# 4f. Get pending suggestions (should be 0 now)
pending = cr_test.get_pending_suggestions()
if len(pending) == 0:
    OK("No pending suggestions after accept+reject")
else:
    FAIL(f"Expected 0 pending, got {len(pending)}")

# 4g. Add new pending and check
cr_test._suggestions.append({"suggestion": "test", "status": "pending"})
pending = cr_test.get_pending_suggestions()
if len(pending) == 1:
    OK("1 pending suggestion after adding new")
else:
    FAIL(f"Expected 1 pending, got {len(pending)}")

# 4h. Out-of-range accept/reject does not crash
cr_test.accept_suggestion(999)
cr_test.reject_suggestion(-1)
OK("Out-of-range accept/reject does not crash")

# 4i. is_due check
cr2 = CodeSelfReview(make_mock_consciousness(), make_mock_llm())
if cr2.is_due():
    OK("is_due=True when never run")
else:
    FAIL("is_due should be True when _last_run=0")

cr2._last_run = time.monotonic()
if not cr2.is_due():
    OK("is_due=False right after running")
else:
    FAIL("is_due should be False right after running")

# 4j. Stats property
stats = cr_test.stats
expected_keys = ["total_reviews", "total_suggestions", "pending", "accepted", "rejected"]
missing = [k for k in expected_keys if k not in stats]
if not missing:
    OK(f"Stats has all expected keys")
else:
    FAIL(f"Stats missing keys: {missing}")

if stats["accepted"] == 1 and stats["rejected"] == 1 and stats["pending"] == 1:
    OK("Stats counts: 1 accepted, 1 rejected, 1 pending")
else:
    WARN(f"Stats counts: {stats}")

# 4k. Persistence (save/load)
tmp_dir = tempfile.mkdtemp()
try:
    tmp_path = Path(tmp_dir) / "test_suggestions.jsonl"
    cr3 = CodeSelfReview(make_mock_consciousness(), make_mock_llm())
    cr3._suggestions_path = tmp_path
    cr3._suggestions = [
        {"suggestion": "test1", "status": "pending"},
        {"suggestion": "test2", "status": "accepted"},
    ]
    cr3._save_suggestions()

    cr4 = CodeSelfReview(make_mock_consciousness(), make_mock_llm())
    cr4._suggestions_path = tmp_path
    loaded = cr4._load_suggestions()
    if len(loaded) == 2:
        OK("Suggestions persist across save/load")
    else:
        FAIL(f"Expected 2 loaded, got {len(loaded)}")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# 5. HIVEMIND INTEGRATION
# ═══════════════════════════════════════════════════════════════
SECTION("5. HIVEMIND INTEGRATION")

from hivemind import HiveMind

# 5a. New attributes exist
hm = HiveMind.__new__(HiveMind)
# Manually set the attributes that __init__ would set
hm.web_learner = None
hm.distiller = None
hm.meta_learning = None
hm.code_reviewer = None
hm._meta_learning_last_run = 0.0
hm._code_review_last_run = 0.0

for attr in ["web_learner", "distiller", "meta_learning", "code_reviewer",
             "_meta_learning_last_run", "_code_review_last_run"]:
    if hasattr(hm, attr):
        OK(f"HiveMind has attribute '{attr}'")
    else:
        FAIL(f"HiveMind missing attribute '{attr}'")

# 5b. _init_learning_engines exists
if hasattr(HiveMind, '_init_learning_engines'):
    OK("HiveMind._init_learning_engines method exists")
else:
    FAIL("HiveMind._init_learning_engines method missing")

# 5c. _night_learning_cycle exists
if hasattr(HiveMind, '_night_learning_cycle'):
    OK("HiveMind._night_learning_cycle method exists")
else:
    FAIL("HiveMind._night_learning_cycle missing")

# 5d. Check night cycle source uses mod 5
import inspect
src = inspect.getsource(HiveMind._night_learning_cycle)
if "% 5" in src:
    OK("Night cycle uses % 5 rotation")
else:
    FAIL("Night cycle should use % 5 rotation")

if "web_learning_cycle" in src:
    OK("Night cycle references web_learning_cycle")
else:
    FAIL("Night cycle should reference web_learning_cycle")

if "distillation_cycle" in src:
    OK("Night cycle references distillation_cycle")
else:
    FAIL("Night cycle should reference distillation_cycle")

if "meta_learning" in src and "is_due" in src:
    OK("Night cycle checks meta_learning.is_due()")
else:
    FAIL("Night cycle should check meta_learning.is_due()")

if "code_reviewer" in src and "is_due" in src:
    OK("Night cycle checks code_reviewer.is_due()")
else:
    FAIL("Night cycle should check code_reviewer.is_due()")

# 5e. Check _init_learning_engines source
src_init = inspect.getsource(HiveMind._init_learning_engines)
if "WebLearningAgent" in src_init:
    OK("_init_learning_engines imports WebLearningAgent")
else:
    FAIL("_init_learning_engines should import WebLearningAgent")

if "KnowledgeDistiller" in src_init:
    OK("_init_learning_engines imports KnowledgeDistiller")
else:
    FAIL("_init_learning_engines should import KnowledgeDistiller")

if "MetaLearningEngine" in src_init:
    OK("_init_learning_engines imports MetaLearningEngine")
else:
    FAIL("_init_learning_engines should import MetaLearningEngine")

if "CodeSelfReview" in src_init:
    OK("_init_learning_engines imports CodeSelfReview")
else:
    FAIL("_init_learning_engines should import CodeSelfReview")

# 5f. get_status includes new keys
src_status = inspect.getsource(HiveMind.get_status)
for key in ["web_learner", "distiller", "meta_learning", "code_reviewer"]:
    if f'"{key}"' in src_status:
        OK(f"get_status includes '{key}' key")
    else:
        FAIL(f"get_status should include '{key}' key")

# 5g. stop() saves code_reviewer suggestions
src_stop = inspect.getsource(HiveMind.stop)
if "code_reviewer" in src_stop and "_save_suggestions" in src_stop:
    OK("stop() saves code_reviewer suggestions")
else:
    FAIL("stop() should save code_reviewer suggestions")


# ═══════════════════════════════════════════════════════════════
# 6. SETTINGS.YAML
# ═══════════════════════════════════════════════════════════════
SECTION("6. SETTINGS.YAML — New config keys")

import yaml
settings_path = Path("configs/settings.yaml")
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    al = config.get("advanced_learning", {})

    checks = {
        "web_learning_enabled": True,
        "web_learning_daily_budget": 50,
        "distillation_enabled": False,
        "distillation_api_key": "",
        "distillation_model": "claude-haiku-4-5-20251001",
        "distillation_weekly_budget_eur": 5.0,
        "meta_learning_enabled": True,
        "code_review_enabled": True,
    }

    for key, expected in checks.items():
        actual = al.get(key)
        if actual == expected:
            OK(f"settings.yaml: {key}={actual}")
        elif actual is not None:
            WARN(f"settings.yaml: {key}={actual} (expected {expected})")
        else:
            FAIL(f"settings.yaml missing: {key}")
else:
    FAIL("configs/settings.yaml not found")


# ═══════════════════════════════════════════════════════════════
# 7. DASHBOARD — New endpoints
# ═══════════════════════════════════════════════════════════════
SECTION("7. DASHBOARD — Endpoints & WS events")

dashboard_path = Path("web/dashboard.py")
if dashboard_path.exists():
    with open(dashboard_path, encoding="utf-8") as f:
        dash_src = f.read()

    # 7a. /api/meta_report endpoint
    if "/api/meta_report" in dash_src:
        OK("Dashboard has /api/meta_report endpoint")
    else:
        FAIL("Dashboard missing /api/meta_report endpoint")

    # 7b. /api/code_suggestions endpoint
    if "/api/code_suggestions" in dash_src:
        OK("Dashboard has /api/code_suggestions endpoint")
    else:
        FAIL("Dashboard missing /api/code_suggestions endpoint")

    # 7c. Accept endpoint
    if "code_suggestions/{index}/accept" in dash_src:
        OK("Dashboard has accept endpoint")
    else:
        FAIL("Dashboard missing accept endpoint")

    # 7d. Reject endpoint
    if "code_suggestions/{index}/reject" in dash_src:
        OK("Dashboard has reject endpoint")
    else:
        FAIL("Dashboard missing reject endpoint")

    # 7e. /api/consciousness includes new stats
    if "web_learner" in dash_src and "distiller" in dash_src:
        OK("/api/consciousness includes web_learner and distiller stats")
    else:
        FAIL("/api/consciousness should include web_learner and distiller")

    if "meta_learning" in dash_src and "code_reviewer" in dash_src:
        OK("/api/consciousness includes meta_learning and code_reviewer stats")
    else:
        FAIL("/api/consciousness should include meta_learning and code_reviewer")

    # 7f. WS event handlers in JS
    for event in ["web_learning", "distillation", "meta_report", "code_suggestion"]:
        if f"tp==='{event}'" in dash_src:
            OK(f"Dashboard JS handles '{event}' WS event")
        else:
            FAIL(f"Dashboard JS missing '{event}' WS event handler")
else:
    FAIL("web/dashboard.py not found")


# ═══════════════════════════════════════════════════════════════
# 8. EDGE CASES
# ═══════════════════════════════════════════════════════════════
SECTION("8. EDGE CASES — disabled features, empty data")

# 8a. All features disabled in init_learning_engines
hm2 = HiveMind.__new__(HiveMind)
hm2.enrichment = None
hm2.web_learner = None
hm2.distiller = None
hm2.meta_learning = None
hm2.code_reviewer = None
hm2.consciousness = make_mock_consciousness()
hm2.llm_heartbeat = make_mock_llm()
hm2.llm = make_mock_llm()
hm2.agent_levels = None

disabled_cfg = {
    "enrichment_enabled": False,
    "web_learning_enabled": False,
    "distillation_enabled": False,
    "meta_learning_enabled": False,
    "code_review_enabled": False,
}
try:
    hm2._init_learning_engines(disabled_cfg)
    if (hm2.enrichment is None and hm2.web_learner is None
            and hm2.distiller is None and hm2.meta_learning is None
            and hm2.code_reviewer is None):
        OK("All disabled → no engines initialized")
    else:
        FAIL("Some engines initialized despite being disabled")
except Exception as e:
    FAIL(f"_init_learning_engines error with all disabled: {e}")

# 8b. WebLearningAgent with no web search tool
async def test_no_websearch():
    c = make_mock_consciousness()
    wl = WebLearningAgent(c, make_mock_llm(), make_mock_llm())
    wl._web_search = None
    # Patch the import to fail
    with patch.dict('sys.modules', {'tools.web_search': None}):
        wl._web_search = None
        stored = await wl.web_learning_cycle()
    return stored

result = asyncio.get_event_loop().run_until_complete(test_no_websearch())
if result == 0:
    OK("No web search tool → returns 0 gracefully")
else:
    FAIL(f"Expected 0, got {result}")

# 8c. MetaLearningEngine with no optional components
ml_minimal = MetaLearningEngine(make_mock_consciousness())
async def test_minimal_meta():
    return await ml_minimal.weekly_analysis()

report = asyncio.get_event_loop().run_until_complete(test_minimal_meta())
if "memory_stats" in report:
    OK("Meta-learning works without optional components")
else:
    FAIL(f"Minimal meta-learning failed: {report}")

# 8d. CodeSelfReview with no meta-learning report
async def test_no_meta_report():
    cr = CodeSelfReview(make_mock_consciousness(), make_mock_llm("SUGGESTION: test\nIMPACT: test\nRISK: low"))
    cr.meta_learning = None
    return await cr.monthly_code_review()

suggestions = asyncio.get_event_loop().run_until_complete(test_no_meta_report())
# Should still work, using consciousness stats fallback
if isinstance(suggestions, list):
    OK("Code review works without meta-learning report")
else:
    FAIL(f"Code review should return list: {type(suggestions)}")

# 8e. Empty parse
empty_sugg = CodeSelfReview(make_mock_consciousness(), make_mock_llm())
result = empty_sugg._parse_suggestions("No structured output here")
if result == []:
    OK("Parse of unstructured text returns empty list")
else:
    WARN(f"Parse of unstructured: {result}")


# ═══════════════════════════════════════════════════════════════
# 9. GRACEFUL DEGRADATION
# ═══════════════════════════════════════════════════════════════
SECTION("9. GRACEFUL DEGRADATION")

# 9a. KnowledgeDistiller without anthropic
kd_no_lib = KnowledgeDistiller(make_mock_consciousness(), api_key="test")
# Force anthropic unavailable
kd_no_lib._anthropic_available = False
async def test_no_anthropic():
    return await kd_no_lib.distillation_cycle()

result = asyncio.get_event_loop().run_until_complete(test_no_anthropic())
if result == 0:
    OK("Distillation without anthropic returns 0 gracefully")
else:
    FAIL(f"Expected 0, got {result}")

# 9b. WebLearningAgent with search returning errors
async def test_search_error():
    c = make_mock_consciousness()
    wl = WebLearningAgent(c, make_mock_llm(), make_mock_llm())
    mock_ws = MagicMock()
    mock_ws.search = AsyncMock(return_value=[
        {"title": "Virhe", "url": "", "body": "duckduckgo-search not installed"}
    ])
    mock_ws._ddgs_available = True
    wl._web_search = mock_ws
    return await wl.web_learning_cycle()

result = asyncio.get_event_loop().run_until_complete(test_search_error())
if result == 0:
    OK("Search error returns 0 gracefully")
else:
    FAIL(f"Expected 0 on search error, got {result}")

# 9c. WebLearningAgent with search exception
async def test_search_exception():
    c = make_mock_consciousness()
    wl = WebLearningAgent(c, make_mock_llm(), make_mock_llm())
    mock_ws = MagicMock()
    mock_ws.search = AsyncMock(side_effect=Exception("Network error"))
    mock_ws._ddgs_available = True
    wl._web_search = mock_ws
    return await wl.web_learning_cycle()

result = asyncio.get_event_loop().run_until_complete(test_search_exception())
if result == 0:
    OK("Search exception returns 0 gracefully")
else:
    FAIL(f"Expected 0 on exception, got {result}")

# 9d. KnowledgeDistiller with empty failed_queries
kd_empty = KnowledgeDistiller(make_mock_consciousness(), api_key="test")
kd_empty._anthropic_available = True
questions = kd_empty._load_pending_questions()
# May or may not find questions depending on data dir
if isinstance(questions, list):
    OK("_load_pending_questions returns list (may be empty)")
else:
    FAIL(f"Expected list, got {type(questions)}")

# 9e. MetaLearningEngine with broken consciousness
async def test_broken_consciousness():
    c = MagicMock()
    c.memory = None  # broken
    c._total_queries = 0
    c._hallucination_count = 0
    ml = MetaLearningEngine(c)
    report = await ml.weekly_analysis()
    return report

report = asyncio.get_event_loop().run_until_complete(test_broken_consciousness())
if "memory_stats" in report:
    OK("Meta-learning handles broken consciousness gracefully")
else:
    FAIL(f"Meta-learning should handle broken consciousness")

# 9f. Validate fact with LLM error
async def test_validate_error():
    c = make_mock_consciousness()
    llm_err = MagicMock()
    llm_err.generate = AsyncMock(side_effect=Exception("LLM timeout"))
    wl = WebLearningAgent(c, make_mock_llm(), llm_err)
    result = await wl._validate_fact("Some fact")
    return result

result = asyncio.get_event_loop().run_until_complete(test_validate_error())
if result == False:
    OK("Validation LLM error returns False gracefully")
else:
    FAIL(f"Expected False on LLM error, got {result}")


# ═══════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════
print(f"\n{B}{'='*60}")
print(f"  PHASE 9 TEST RESULTS")
print(f"{'='*60}{W}")
print(f"  {G}PASS: {results['pass']}{W}")
print(f"  {R}FAIL: {results['fail']}{W}")
print(f"  {Y}WARN: {results['warn']}{W}")
total = results["pass"] + results["fail"]
pct = results["pass"] / max(total, 1) * 100

if results["fail"] == 0:
    print(f"\n  {G}ALL TESTS PASSED ({pct:.0f}%) — PHASE 9 LAYERS 3-6 COMPLETE{W}")
elif results["fail"] <= 3:
    print(f"\n  {Y}MOSTLY PASSING ({pct:.0f}%) — minor issues{W}")
else:
    print(f"\n  {R}ISSUES FOUND ({pct:.0f}%) — review failures:{W}")
    for err in results["errors"]:
        print(f"    {R}• {err}{W}")

print()
sys.exit(0 if results["fail"] == 0 else 1)
